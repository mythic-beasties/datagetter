#!/bin/bash
# This follows broadly the approach from
# http://www.kennethreitz.org/essays/a-better-pip-workflow
rm -rf .ve
virtualenv --python=python3 .ve
source .ve/bin/activate
if [[ "$1" == "--new-only" ]]; then
    # If --new-only is supplied then we install the current versions of
    # packages into the virtualenv, so that the only change will be any new
    # packages and their dependencies.
    pip install -r requirements.txt
    dashupgrade=""
else
    dashupgrade="--upgrade"
fi
pip install $dashupgrade -r requirements.in
pip freeze -r requirements.in | grep -v 'pkg-resources' > requirements.txt
# Put comments back on the same line (mostly for requires.io's benefit)
sed -i '$!N;s/\n#\^\^/ #/;P;D' requirements*txt
sed -i 's/^-r.*//' requirements*txt
