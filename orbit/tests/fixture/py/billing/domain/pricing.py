"""Pricing rules. A Kafka mention in a docstring must not become a ref."""
import requests
import json, os as operating_system

from billing.data.store import Store


class Pricer(Rule):
    pass


class Taxer(Rule, Auditable):
    def apply(self):
        return Store()
