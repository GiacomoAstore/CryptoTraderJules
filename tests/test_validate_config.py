import pytest
import yaml

from validate_config import AppConfig, validate_config_dict, validate_config_yaml


VALID_CONFIG = """
strategies:
  - name: "EMAStrategy"
    enabled: true
    weight: 1
    variant_a:
      fast_period: 5
      slow_period: 20
risk:
  max_open_positions: 3
consensus:
  threshold: 2
"""


def test_valid_config_parses():
    cfg = validate_config_yaml(VALID_CONFIG)
    assert len(cfg.strategies) == 1
    assert cfg.consensus.threshold == 2


def test_unknown_strategy_rejected():
    data = yaml.safe_load(VALID_CONFIG)
    data["strategies"][0]["name"] = "FakeStrategy"
    with pytest.raises(Exception):
        validate_config_dict(data)


def test_missing_variants_rejected():
    data = yaml.safe_load(VALID_CONFIG)
    del data["strategies"][0]["variant_a"]
    with pytest.raises(Exception):
        validate_config_dict(data)


def test_repo_config_yaml_is_valid():
  import os
  path = os.path.join(os.path.dirname(__file__), "..", "shared_config", "config.yaml")
  cfg = validate_config_yaml(open(path, encoding="utf-8").read())
  assert len(cfg.strategies) >= 1
