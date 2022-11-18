#!/usr/bin/env python3

"""
Based on code from:

- https://ubuntu-mate.community/t/my-first-panel-applet/19769
- https://github.com/mate-desktop-legacy-archive/mate-university/blob/master/applet-python/university-python-applet
- https://python-gtk-3-tutorial.readthedocs.io/en/latest/combobox.html
- https://www.micahcarrick.com/gsettings-python-gnome-3.html
- https://api.exchangerate.host
- https://dev.to/vladned/calculating-the-number-of-seconds-until-midnight-383d
- https://stackoverflow.com/a/45986036/586382
"""

from datetime import date, datetime, timedelta, time, tzinfo
from json import dumps, loads
from locale import LC_MONETARY, localeconv, setlocale
from threading import Timer
from urllib.request import urlopen
from xml.etree import ElementTree

from gi import require_version
from gi.repository.Gio import Settings
from gi.repository.Gtk import ComboBoxText, Grid, SpinButton
from gi.repository.MatePanelApplet import Applet


base_url = 'https://api.exchangerate.host'
URL_ISO4217 = "https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml"


require_version("Gtk", "3.0")
require_version('MatePanelApplet', '4.0')


class MateCurrencyConverterApplet(Applet):
    def __init__(self, applet):
        # Get and config settings store
        settings = Settings(applet.get_preferences_path())
        settings.connect(
            "changed::currency_base", self._on_settings_currency_base_changed,
            currency_base, currency_secondary
        )
        settings.connect(
            "changed::currency_secondary",
            self._on_settings_currency_secondary_changed, currency_base,
            currency_secondary
        )
        settings.connect(
            "changed::date", self._fetch_rate, currency_base,
            currency_secondary
        )
        settings.connect(
            "changed::quantity", self._on_settings_quantity_changed
        )
        settings.connect("changed::rate", self._convert)
        settings.connect(
            "changed::symbols", self._on_settings_symbols_changed,
            currency_base, quantity_base, currency_secondary,
            quantity_secondary
        )
        settings.connect(
            "changed::_quantities_order_inverted",
            self._on_settings_quantities_order_inverted_changed
        )

        # Create grid for applet layout
        grid = Grid()

        # Create and init widgets
        quantity_base = SpinButton()
        quantity_base.connect(
            "value-changed", self._on_quantity_changed, quantity_secondary,
            false, settings
        )

        # TODO: Show symbol on applet, and symbol + full name on dropdown
        currency_base = ComboBoxText()
        currency_base.connect(
            "changed", self._on_currency_changed, currency_base,
            currency_secondary, settings
        )

        quantity_secondary = SpinButton()
        quantity_secondary.connect(
            "value-changed", self._on_quantity_changed, quantity_base, true,
            settings
        )

        # TODO: Show symbol on applet, and symbol + full name on dropdown
        currency_secondary = ComboBoxText()
        currency_secondary.connect(
            "changed", self._on_currency_changed, currency_base,
            currency_secondary, settings
        )

        # Re-hydrate stored settings
        quantity = settings.get_float("quantity")
        if settings.get_boolean("_quantities_order_inverted"):
            quantity_base.set_value(quantity)
        else:
            quantity_secondary.set_value(quantity)

        self._symbols_changed(
            currency_base, quantity_base, currency_secondary,
            quantity_secondary, settings
        )
        self._convert(settings)

        # Add widgets to grid
        grid.attach(quantity_base     , 0, 0, 1, 1)
        grid.attach(currency_base     , 0, 1, 1, 1)
        grid.attach(quantity_secondary, 1, 0, 1, 1)
        grid.attach(currency_secondary, 1, 1, 1, 1)

        # Add grid to applet and show it
        applet.set_border_width(0)
        applet.add(grid)
        applet.show_all()

        # Keep rates and symbols updated on each day changes
        self._fetch(
            quantity_base, currency_base, quantity_secondary,
            currency_secondary, settings
        )

    # Object API
    def __del__(self):
        try:
            self._timer.cancel()

    # Private methods
    def _convert(self, settings):
        currency_base = self._currency_base.get_active_text()
        currency_secondary = self._currency_secondary.get_active_text()

        rate = settings.get_float("rate")

        self.set_tooltip_text(
            f"1 {currency_base} = {rate} {currency_secondary}\n"
            f"1 {currency_secondary} = {1/rate} {currency_base}"
        )

        quantity_base = self._quantity_base
        quantity_secondary = self._quantity_secondary

        if settings.get_boolean("_quantities_order_inverted"):
            quantity_base.set_value(quantity_secondary.get_value() / rate)
        else:
            quantity_secondary.set_value(quantity_base.get_value() * rate)

    def _fetch(
        self, quantity_base, currency_base, quantity_secondary,
        currency_secondary, settings
    ):
        """Keep rates and symbols updated on each day changes"""
        # Fetch and update symbols
        self._fetch_symbols(currency_base, currency_secondary, settings)
        self._symbols_changed(
            currency_base, quantity_base, currency_secondary,
            quantity_secondary, settings
        )

        # Fetch rate
        self._fetch_rate(currency_base, currency_secondary, settings)

        now = datetime.now()
        # Historical exchange rates are available at 00:05am GMT (UTC), see
        # https://exchangerate.host/#/#faq
        midnight = datetime.combine(
            now + timedelta(days=1), time(0, 5), timezone.utc
        )
        seconds_until_midnight = (midnight - now).seconds

        timer = Timer(
            seconds_until_midnight, self._fetch,
            [
                quantity_base, currency_base, quantity_secondary,
                currency_secondary, settings
            ]
        )
        timer.start()

        self._timer = timer

    def _fetch_rate(self, currency_base, currency_secondary, settings):
        # Check if currencies are different to previous ones, or date changed
        today = date.today().isoformat()
        if (
            settings.get_string("currency_base")      == currency_base      &&
            settings.get_string("currency_secondary") == currency_secondary &&
            settings.get_string("date")               == today
        ):
            return

        # Fetch updated rate
        # TODO: detect network failures and retry on reconnect

        with urlopen(
            f"{base_url}/latest?"
            f"base={currency_base}&symbols={currency_secondary}"
        ) as f:
            res_body = f.read()

        json = loads(res_body.decode("utf-8"))
        rate = json["rates"][currency_secondary]

        settings.set_string("date", json["date"])
        settings.set_text("currency_base", currency_base)
        settings.set_text("currency_secondary", currency_secondary)
        settings.set_float("rate", rate)

        # Convert currencies with updated rate
        self._convert(settings)

    def _fetch_symbols(self, currency_base, currency_secondary, settings):
        # TODO: detect network failures and retry on reconnect

        # Get currencies definitions
        with urlopen(URL_ISO4217) as f:
            res_body = f.read()

        root = ET.fromstring(res_body.decode("utf-8"))

        iso4217 = {
            CcyNtry['Ccy'].text: CcyNtry['CcyMnrUnts'].text
                for CcyNtry in root[0]
                if CcyNtry['Ccy']
        }

        # Get available currencies
        with urlopen(f"{base_url}/symbols") as f:
            res_body = f.read()

        json = loads(res_body.decode("utf-8"))
        symbols = {
            k: v for k, v in iso4217.iteritems() if k in json["symbols"].keys()
        }

        settings.set_string("symbols", dumps(symbols))

    def _symbols_changed(
        self, currency_base, quantity_base, currency_secondary,
        quantity_secondary, settings
    ):
        symbols = loads(settings.get_string("symbols"))

        # Set quantities
        quantity_base.set_digits(symbols[settings.get_string("currency_base")])
        quantity_secondary.set_digits(
            symbols[settings.get_string("currency_secondary")]
        )

        # Set currencies
        currency_base.remove_all()
        currency_secondary.remove_all()

        for symbol in symbols.keys():
            currency_base.append(symbol, symbol)
            currency_secondary.append(symbol, symbol)

        currency_base.set_active_id(settings.get_string("currency_base"))
        currency_secondary.set_active_id(
            settings.get_string("currency_secondary")
        )

    # Widgets events
    def _on_currency_changed(
        self, currency, currency_base, currency_secondary, settings
    ):
        if currency.get_active_text() is None:
            return

        self._fetch_rate(currency_base, currency_secondary, settings)

    def _on_quantity_changed(
        self, quantity, quantities_order_inverted, settings
    ):
        settings.set_boolean(
            "_quantities_order_inverted", quantities_order_inverted
        )
        settings.set_float("quantity", quantity.get_value())

        self._convert(settings)

    # Settings events
    def _on_settings_currency_base_changed(
        self, settings, currency_base, currency_secondary
    ):
        currency_base.set_value(settings.get_string("currency_base"))

        self._fetch_rate(currency_base, currency_secondary, settings)

    def _on_settings_currency_secondary_changed(
        self, settings, currency_base, currency_secondary
    ):
        currency_secondary.set_value(settings.get_string("currency_secondary"))

        self._fetch_rate(currency_base, currency_secondary, settings)

    def _on_settings_quantity_changed(self, settings):
        quantity = settings.get_float("quantity")

        if settings.get_boolean("_quantities_order_inverted"):
            self._quantity_secondary.set_value(quantity)
        else:
            self._quantity_base.set_value(quantity)

        self._convert(settings)

    def _on_settings_symbols_changed(
        self, settings, currency_base, quantity_base, currency_secondary,
        quantity_secondary
    ):
        self._symbols_changed(
            currency_base, quantity_base, currency_secondary,
            quantity_secondary, settings
        )


def applet_factory(applet, iid, data):
    if iid != "CurrencyConverterApplet":
       return False

    # Create applet instance
    MateCurrencyConverterApplet(applet)

    return True


if __name__ == "__main__":
    Applet.factory_main(
        "CurrencyConverterAppletFactory", True, Applet.__gtype__,
        applet_factory, None
    )
