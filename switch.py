"""Support for Sure PetCare Flaps/Pets binary sensors."""
from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from surepy.entities import SurepyEntity
from surepy.entities.devices import Feeder as SureFeeder
from surepy.entities.pet import Pet as SurePet
from surepy.enums import EntityType
from surepy.const import (
    BASE_RESOURCE,
    DEVICE_TAG_RESOURCE,
)

# pylint: disable=relative-beyond-top-level
from . import SurePetcareAPI
from .const import DOMAIN, SPC, SURE_MANUFACTURER

PARALLEL_UPDATES = 2


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigEntry,
    async_add_entities: Any,
    discovery_info: Any = None,
) -> None:
    """Set up Sure PetCare binary-sensor platform."""
    await async_setup_entry(hass, config, async_add_entities)


async def async_setup_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities: Any
) -> None:
    """Set up config entry Sure PetCare Flaps sensors."""

    entities: list[SurePetcareSwitch] = []

    spc: SurePetcareAPI = hass.data[DOMAIN][SPC]

    pets: list[SurePet] = [e for e in spc.coordinator.data.values() if e.type == EntityType.PET]
    feeders: list[SureFeeder] = [e for e in spc.coordinator.data.values() if e.type == EntityType.FEEDER]

    for p in pets:
        for f in feeders:
            if p.household_id != f.household_id:
                continue
            entities.append(PetFeederAccess(
                spc.coordinator, p.id, f.id, spc
            ))

    async_add_entities(entities, True)


class SurePetcareSwitch(CoordinatorEntity, SwitchEntity):
    """A binary sensor implementation for Sure Petcare Entities."""

    _attr_should_poll = False

    def __init__(
        self,
        coordinator,
        _id: int,
        spc: SurePetcareAPI,
    ):
        """Initialize a Sure Petcare binary sensor."""
        super().__init__(coordinator)

        self._id: int = _id
        self._spc: SurePetcareAPI = spc

        self._coordinator = coordinator

        self._surepy_entity: SurepyEntity = self._coordinator.data[self._id]
        self._state: Any = self._surepy_entity.raw_data().get("status", {})

        type_name = self._surepy_entity.type.name.replace("_", " ").title()

        self._name: str = (
            # cover edge case where a device has no name set
            # (dont know how to do this but people have managed to do it  ¯\_(ツ)_/¯)
            self._surepy_entity.name
            if self._surepy_entity.name
            else f"Unnamed {type_name}"
        )

        self._attr_available = bool(self._state)

        self._attr_name: str = f"{type_name} {self._name}"
        self._attr_unique_id = f"{self._surepy_entity.household_id}-{self._id}"

        if self._state:
            self._attr_extra_state_attributes = {**self._surepy_entity.raw_data()}

    @property
    def device_info(self):

        device = {}

        try:

            model = f"{self._surepy_entity.type.name.replace('_', ' ').title()}"
            if serial := self._surepy_entity.raw_data().get("serial_number"):
                model = f"{model} ({serial})"
            elif mac_address := self._surepy_entity.raw_data().get("mac_address"):
                model = f"{model} ({mac_address})"
            elif tag_id := self._surepy_entity.raw_data().get("tag_id"):
                model = f"{model} ({tag_id})"

            device = {
                "identifiers": {(DOMAIN, self._id)},
                "name": self._surepy_entity.name.capitalize(),
                "manufacturer": SURE_MANUFACTURER,
                "model": model,
            }

            if self._state:
                versions = self._state.get("version", {})

                if dev_fw_version := versions.get("device", {}).get("firmware"):
                    device["sw_version"] = dev_fw_version

                if (lcd_version := versions.get("lcd", {})) and (
                    rf_version := versions.get("rf", {})
                ):
                    device["sw_version"] = (
                        f"lcd: {lcd_version.get('version', lcd_version)['firmware']} | "
                        f"fw: {rf_version.get('version', rf_version)['firmware']}"
                    )

        except AttributeError:
            pass

        return device


class PetFeederAccess(SurePetcareSwitch):
    """Sure Petcare Pet."""

    def __init__(self, coordinator, _pet_id: int, _feeder_id: int, spc: SurePetcareAPI) -> None:
        """Initialize a Sure Petcare Hub."""
        super().__init__(coordinator, _pet_id, spc)

        self._feeder_id: int = _feeder_id

        feeder: SureFeeder = coordinator.data[_feeder_id]

        self._attr_name = f"{self._attr_name} {feeder.name} Access"

        self._attr_unique_id = (
            f"{self._surepy_entity.household_id}-{self._surepy_entity.id}-{feeder.id}-feeder-access"
        )

    @property
    def is_on(self) -> bool | None:
        """Return True if the hub is on."""

        pet: SurePet
        feeder: SureFeeder

        if (pet := self._coordinator.data[self._id]) and (feeder := self._coordinator.data[self._feeder_id]):
            return pet.tag_id in {t.id for t in feeder.tags.values()}

        return None

    async def async_turn_on(self):
        pet = self._coordinator.data[self._id]
        await _add_tag_to_device(self._spc, self._feeder_id, pet.tag_id)
        while not self.is_on:
            await asyncio.sleep(2)
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self):
        pet = self._coordinator.data[self._id]
        await _remove_tag_from_device(self._spc, self._feeder_id, pet.tag_id)
        while self.is_on:
            await asyncio.sleep(2)
            await self.coordinator.async_request_refresh()


async def _add_tag_to_device(spc: SurePetcareAPI, device_id: int, tag_id: int) -> dict[str, Any] | None:
    """Add the specified tag ID to the specified device ID"""
    resource = DEVICE_TAG_RESOURCE.format(
        BASE_RESOURCE=BASE_RESOURCE, device_id=device_id, tag_id=tag_id
    )

    if response := await spc.surepy.sac.call(method="PUT", resource=resource, data={}):
        return response


async def _remove_tag_from_device(spc: SurePetcareAPI, device_id: int, tag_id: int) -> dict[str, Any] | None:
    """Removes the specified tag ID from the specified device ID"""
    resource = DEVICE_TAG_RESOURCE.format(
        BASE_RESOURCE=BASE_RESOURCE, device_id=device_id, tag_id=tag_id
    )

    if response := await spc.surepy.sac.call(method="DELETE", resource=resource):
        return response
