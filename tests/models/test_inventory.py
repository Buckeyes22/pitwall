from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from pitwall.models.inventory import LocalInventory, load_inventory


def test_inventory_loads_wrapped_yaml(tmp_path) -> None:
    path = tmp_path / "inventory.yaml"
    path.write_text(
        "inventory:\n"
        "  gpus:\n"
        "    - {name: <gpu-name>, count: 2, vram_gb: 24, arch: sm_86, "
        "nvlink: false}\n"
        "  gpu_memory_utilization: 0.90\n",
        encoding="utf-8",
    )
    inventory = load_inventory(path)
    assert inventory.gpus[0].count == 2
    assert inventory.gpu_memory_utilization == Decimal("0.90")


def test_inventory_rejects_bad_arch_and_fraction() -> None:
    with pytest.raises(ValidationError):
        LocalInventory.model_validate(
            {
                "gpus": [
                    {
                        "name": "<gpu-name>",
                        "count": 1,
                        "vram_gb": 24,
                        "arch": "86",
                        "nvlink": False,
                    }
                ],
                "gpu_memory_utilization": 1.1,
            }
        )


def test_inventory_yaml_error_never_quotes_the_file(tmp_path) -> None:
    path = tmp_path / "inventory.yaml"
    path.write_text("inventory:\n  token: hunter2\n  api_key: sk-SECRET123: x\n", encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        load_inventory(path)
    message = str(caught.value)
    assert "inventory.yaml" in message
    assert "line 3, column 24" in message
    assert "sk-SECRET123" not in message
    assert "hunter2" not in message
    assert caught.value.__cause__ is None
