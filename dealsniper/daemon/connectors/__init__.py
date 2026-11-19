"""Registre des connecteurs : pour ajouter une source, créez un fichier ici et ajoutez sa classe à REGISTRY."""

from .base import Connector
from .ebay import EbayConnector
from .email import EmailConnector
from .example_site import ExampleSiteConnector
from .rss import RssConnector

REGISTRY: dict[str, type[Connector]] = {
    cls.name: cls for cls in (EbayConnector, EmailConnector, RssConnector, ExampleSiteConnector)
}

__all__ = ["Connector", "REGISTRY"]
