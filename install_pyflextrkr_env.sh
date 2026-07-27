#!/usr/bin/env bash
set -euo pipefail

ENV_NAME="${PYFLEXTRKR_ENV_NAME:-xgbffp-pyflextrkr}"
UPSTREAM_COMMIT="6a3a6435ee6b3a64ec411b9f2af38226d6f32850"

conda create -y -n "$ENV_NAME" -c conda-forge python=3.12 pip
conda run -n "$ENV_NAME" python -m pip install \
  "git+https://github.com/FlexTRKR/PyFLEXTRKR.git@${UPSTREAM_COMMIT}"
conda run -n "$ENV_NAME" python -c \
  'import importlib.metadata; print("PyFLEXTRKR", importlib.metadata.version("pyflextrkr"))'
