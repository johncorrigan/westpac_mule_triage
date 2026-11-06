# mule_triage

A command-line tool, being built, to help a bank's financial-crime investigators triage possible
money-mule accounts in one day of synthetic payments. It's meant to explain every alert with its
evidence and never take an action on an account; so far it's a skeleton with no commands.

With Python 3.11 or newer, one command sets everything up and runs every check:

```sh
python3 scripts/quickstart.py
```

It runs these four steps, which also work by hand:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -c constraints.txt -e ".[dev]"
python scripts/verify.py
```
