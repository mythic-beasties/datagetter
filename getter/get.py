import itertools
import json
import os
import shutil
import tempfile
import traceback
from multiprocessing.dummy import Pool

import flattentool
import requests
import email.headerregistry  # (content-disposition header parser)
import strict_rfc3339
from jsonschema import validate, ValidationError, FormatChecker

acceptable_licenses = [
    'http://www.opendefinition.org/licenses/odc-pddl',
    'https://creativecommons.org/publicdomain/zero/1.0/',
    'https://www.nationalarchives.gov.uk/doc/open-government-licence/version/2/',
    'http://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/',
    'https://creativecommons.org/licenses/by/4.0/',
    'https://creativecommons.org/licenses/by-sa/3.0/',
    'https://creativecommons.org/licenses/by-sa/4.0/',
]

unacceptable_licenses = [
    '',
    # Not relicenseable as CC-BY
    'https://www.nationalarchives.gov.uk/doc/open-government-licence/version/1/', 
    'https://creativecommons.org/licenses/by-nc/4.0/',
    'https://creativecommons.org/licenses/by-nc-sa/4.0/',
]

CONTENT_TYPE_MAP = {
    'application/json': 'json',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
    'text/csv': 'csv',
    "application/vnd.oasis.opendocument.spreadsheet": "ods"
}

package_schema = json.loads(requests.get('https://raw.githubusercontent.com/ThreeSixtyGiving/standard/master/schema/360-giving-package-schema.json').text)

data_valid = []
data_acceptable_license = []
data_acceptable_license_valid = []


def convert_spreadsheet(input_path, converted_path, file_type, schema_path):
    encoding = 'utf-8-sig'
    if file_type == 'csv':
        tmp_dir = tempfile.mkdtemp()
        destination = os.path.join(tmp_dir, 'grants.csv')
        shutil.copy(input_path, destination)
        try:
            with open(destination, encoding='utf-8-sig') as main_sheet_file:
                main_sheet_file.read()
        except UnicodeDecodeError:
            try:
                with open(destination, encoding='cp1252') as main_sheet_file:
                    main_sheet_file.read()
                encoding = 'cp1252'
            except UnicodeDecodeError:
                encoding = 'latin_1'
        input_name = tmp_dir
    else:
        input_name = input_path

    flattentool.unflatten(
        input_name,
        output_name=converted_path,
        input_format=file_type,
        root_list_path='grants',
        root_id='',
        schema=schema_path,
        convert_titles=True,
        encoding=encoding,
        metatab_schema='https://raw.githubusercontent.com/ThreeSixtyGiving/standard/master/schema/360-giving-package-schema.json',
        metatab_name='Meta',
        metatab_vertical_orientation=True,
    )


def mkdirs(data_dir, exist_ok=False):
    os.makedirs(data_dir, exist_ok=exist_ok)
    for dir_name in ['original', 'json_all', 'json_valid',
                     'json_acceptable_license',
                     'json_acceptable_license_valid']:
        os.makedirs("%s/%s" % (data_dir, dir_name), exist_ok=exist_ok)


def fetch_and_convert(args, dataset, schema_path):
    r = None

    metadata = dataset.get('datagetter_metadata', {})
    dataset['datagetter_metadata'] = metadata

    if not dataset['license'] in acceptable_licenses + unacceptable_licenses:
        raise ValueError('Unrecognised license '+dataset['license'])

    url = dataset['distribution'][0]['downloadURL']

    if args.download:
        proxies = None
        metadata['datetime_downloaded'] = strict_rfc3339.now_to_rfc3339_localoffset()
        if args.socks5_proxy:
            proxies = {
                'http': args.socks5_proxy,
                'https': args.socks5_proxy,
            }

        try:
            print("Fetching %s" % url)
            r = requests.get(
                url,
                headers={'User-Agent': 'datagetter (https://github.com/ThreeSixtyGiving/datagetter)'},
                proxies=proxies
            )
            r.raise_for_status()

            metadata['downloads'] = True
        except Exception as e:
            if isinstance(e, KeyboardInterrupt):
                raise

            print("\n\nDownload {} failed for dataset {}\n".format(url, dataset['identifier']))
            traceback.print_exc()
            metadata['downloads'] = False
            metadata['error'] = str(e)

            if not isinstance(e, requests.exceptions.HTTPError):
                return

        content_type = r.headers.get('content-type', '').split(';')[0].lower()
        if content_type and content_type in CONTENT_TYPE_MAP:
            file_type = CONTENT_TYPE_MAP[content_type]
        elif 'content-disposition' in r.headers:
            content_disposition = r.headers.get('content-disposition')
            filename = dict(email.headerregistry.parser.parse_content_disposition_header(
                content_disposition).params).get('filename')
            file_type = filename.split('.')[-1]
        else:
            file_type = url.split('.')[-1]
        if file_type not in CONTENT_TYPE_MAP.values():
            print("\n\nUnrecognised file type {}\n".format(file_type))
            return

        # Check that the downloaded json file is valid json and not junk from the webserver
        # e.g. a 500 error being output without the proper status code.
        if file_type == "json":
            try:
                json.loads(r.text)
            except ValueError:
                print("\n\nJSON file provided by webserver is invalid")
                metadata['downloads'] = False
                metadata['error'] = "Invalid JSON file provided by webserver"
                return

        metadata['file_type'] = file_type

        file_name = args.data_dir+'/original/'+dataset['identifier']+'.'+file_type
        with open(file_name, 'wb') as fp:
            fp.write(r.content)
    else:
        # --no-download arg

        # We require the metadata to exist, it won't if the file failed to download correctly
        if metadata['downloads'] == False:
            print("Skipping %s as it was not marked as successfully downloaded" % dataset['identifier'])
            return

        file_type = metadata['file_type']
        file_name = args.data_dir+'/original/'+dataset['identifier']+'.'+file_type

    json_file_name = '{}/json_all/{}.json'.format(args.data_dir, dataset['identifier'])

    metadata['file_size'] = os.path.getsize(file_name)

    if args.convert and (
            args.convert_big_files or
            metadata['file_size'] < 10 * 1024 * 1024
            ):
        if file_type == 'json':
            os.link(file_name, json_file_name)
            metadata['json'] = json_file_name
        else:
            try:
                print("Running convert on %s to %s" % (file_name,
                                                       json_file_name))
                convert_spreadsheet(
                    file_name,
                    json_file_name,
                    file_type,
                    schema_path
                    )
            except KeyboardInterrupt:
                raise
            except Exception:
                print("\n\nUnflattening failed for file {}\n".format(file_name))
                traceback.print_exc()
                metadata['json'] = None
                metadata["valid"] = False
                metadata["error"] = "Could not unflatten file"
            else:
                metadata['json'] = json_file_name

    metadata['acceptable_license'] = dataset['license'] in acceptable_licenses

    # We can only do anything with the JSON if it did successfully convert.
    if metadata.get('json'):
        format_checker = FormatChecker()
        if args.validate:
            try:
                with open(json_file_name, 'r') as fp:
                    validate(json.load(fp), package_schema, format_checker=format_checker)
            except (ValidationError, ValueError):
                metadata['valid'] = False
            else:
                metadata['valid'] = True

        if metadata['valid']:
            os.link(json_file_name,
                    '{}/json_valid/{}.json'.format(args.data_dir, dataset['identifier']))
            data_valid.append(dataset)
            if metadata['acceptable_license']:
                os.link(json_file_name,
                        '{}/json_acceptable_license_valid/{}.json'.format(args.data_dir, dataset['identifier']))
                data_acceptable_license_valid.append(dataset)

        if metadata['acceptable_license']:
            os.link(json_file_name,
                    '{}/json_acceptable_license/{}.json'.format(args.data_dir, dataset['identifier']))
            data_acceptable_license.append(dataset)


def file_cache_schema():
    tmp_dir = tempfile.mkdtemp()
    schema_path = os.path.join(tmp_dir, '360-giving-schema.json')
    try: 
        print("\nDownloading 360Giving Schema...\n")
        schema = requests.get('https://raw.githubusercontent.com/ThreeSixtyGiving/standard/master/schema/360-giving-schema.json')
    except Exception as e:
        print("Download failed for 360Giving Schema\n")
        traceback.print_exc()

    with open(schema_path, 'w') as fp:
        fp.write(schema.text)

    print("Schema Download successful.\n")
    return schema_path


def get(args):

    if args.local_registry or not args.download:
        mkdirs(args.data_dir, True)
        data_all = json.load(open('%s/data_all.json' % args.data_dir))

    elif args.download:
        mkdirs(args.data_dir, False)
        r = requests.get('https://data.threesixtygiving.org/data.json')
        with open('%s/data_original.json' % args.data_dir, 'w') as fp:
            fp.write(r.text)
        data_all = r.json()

    else:
        print("No source for data")
        exit(1)


    if args.limit_downloads:
        data_all = data_all[:args.limit_downloads]

    schema_path = file_cache_schema()

    with Pool(args.threads) as process_pool:
        process_pool.starmap(fetch_and_convert, zip(itertools.repeat(args),
                                                    data_all, itertools.repeat(schema_path)))

    # Output data.json after every dataset, to help with debugging if we fail
    # part way through
    with open('%s/data_all.json' % args.data_dir, 'w') as fp:
        json.dump(data_all, fp, indent=4)
    with open('%s/data_valid.json' % args.data_dir, 'w') as fp:
        json.dump(data_valid, fp, indent=4)
    with open('%s/data_acceptable_license.json' % args.data_dir, 'w') as fp:
        json.dump(data_acceptable_license, fp, indent=4)
    with open('%s/data_acceptable_license_valid.json' % args.data_dir, 'w') as fp:
        json.dump(data_acceptable_license_valid, fp, indent=4)
