"""Print runtime diagnostics without loading model weights or exposing credentials."""
import sys
import json
from importlib.metadata import version
import torch

if sys.prefix == sys.base_prefix:
    raise SystemExit('Use the project virtual environment: .venv/bin/python scripts/check_environment.py')
print(json.dumps({
    'executable': sys.executable, 'virtual_environment': sys.prefix,
    'python': sys.version.split()[0],
    'packages': {name: version(name) for name in ['torch', 'transformers', 'sentence-transformers', 'qdrant-client', 'fastembed', 'pymupdf']},
    'cuda_available': torch.cuda.is_available(), 'torch_cuda': torch.version.cuda,
    'gpus': [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())],
}, indent=2))
