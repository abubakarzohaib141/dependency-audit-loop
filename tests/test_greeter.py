"""Fully offline tests - no real network calls, so they're deterministic
across many repeated audit-loop runs."""

import click
import requests
from click.testing import CliRunner

from greeter import greet


def test_click_is_importable_and_versioned():
    assert click.__version__


def test_requests_get_is_callable():
    assert callable(requests.get)


def test_greet_default():
    runner = CliRunner()
    result = runner.invoke(greet, [])
    assert result.exit_code == 0
    assert "Hello, World!" in result.output


def test_greet_custom_name():
    runner = CliRunner()
    result = runner.invoke(greet, ["--name", "Ada"])
    assert result.exit_code == 0
    assert "Hello, Ada!" in result.output
