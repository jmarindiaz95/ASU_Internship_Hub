#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  print 'The project Python environment is missing. Follow the setup steps in README.md.'
  read -r '?Press Return to close…'
  exit 1
fi
exec .venv/bin/python launch_hub.py
