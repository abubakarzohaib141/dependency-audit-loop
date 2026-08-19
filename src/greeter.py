"""Tiny CLI app used as the audit target. Exercises click and requests."""

import click
import requests


@click.command()
@click.option("--name", default="World")
def greet(name):
    click.echo(f"Hello, {name}!")


def fetch_status(url):
    """Return the HTTP status code for a GET request to url."""
    response = requests.get(url, timeout=5)
    return response.status_code


if __name__ == "__main__":
    greet()
