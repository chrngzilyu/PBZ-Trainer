"""Every reflected field offset and fixed engine layout used by the trainer.

After a game update, compare these values with the new reflection dump and
edit only this file (plus profile.json).  Field names must match the game's
reflected property names exactly, including spaces.  Nothing here performs
I/O; the values are only numbers.
"""
from dataclasses import dataclass

# FProperty class-cast flags that property() checks.
T_INT = 0x80
T_OBJ = 0x10000
T_FLOAT_ATTR = 0x100000          # FGameplayAttributeData (struct property)
T_DOUBLE = 0x100000000
T_MAP = 0x400000000000


@dataclass(frozen=True)
class Field:
    owner: str      # reflected class the field belongs to (documentation / tests only)
    name: str       # reflected name, must match the game
    offset: int
    kind: int
    size: int

    @property
    def args(self):
        """Arguments for Game.property(cls, *field.args)."""
        return self.name, self.offset, self.kind, self.size


@dataclass(frozen=True)
class AttrField(Field):
    """FGameplayAttributeData: +0 vtable, +8 BaseValue, +12 CurrentValue (16 bytes)."""
    kind: int = T_FLOAT_ATTR
    size: int = 16

    @property
    def base(self):
        """Start of the '<ff' Base+Current pair the trainer writes."""
        return self.offset + 8

    @property
    def current(self):
        """CurrentValue, read as '<f'."""
        return self.offset + 12


def _attr(name, offset):
    return AttrField('BasicAttributeSet', name, offset)


# BasicAttributeSet
HEALTH = _attr('Health', 0x58)
HEALTH_MAX = _attr('Health_Max', 0x68)
ATTACK = _attr('Attack', 0xc8)
TAKEN_PHYSICAL = _attr('TakenVulnerabilityMultiplier_Physical', 0x138)
TAKEN_ELEMENTAL = _attr('TakenVulnerabilityMultiplier_Elemental', 0x2c8)
SHACHI = _attr('Shachi', 0x638)
SHACHI_MAX = _attr('Shachi_Max', 0x658)
ULTIMATE_CHARGE = _attr('UltimateCharge', 0x898)
ULTIMATE_CHARGE_MAX = _attr('UltimateCharge_Max', 0x8a8)
ULTIMATE_COUNT = _attr('UltimateChargeCount', 0x8b8)
ULTIMATE_COUNT_MAX = _attr('UltimateChargeCount_Max', 0x8c8)
SPIRIT_SHADOW = _attr('SpiritShadow', 0x8f8)
SPIRIT_SHADOW_MAX = _attr('SpiritShadow_Max', 0x908)
HEALTH_MIN = _attr('Health_Min', 0xd78)
WALK_SPEED = _attr('WalkSpeed', 0xd88)
JOG_SPEED = _attr('JogSpeed', 0xd98)
SPRINT_SPEED = _attr('SprintSpeed', 0xda8)
CROUCH_SPEED = _attr('CrouchSpeed', 0xdb8)

ATTRIBUTES = (HEALTH, HEALTH_MAX, ATTACK, TAKEN_PHYSICAL, TAKEN_ELEMENTAL, SHACHI, SHACHI_MAX,
              ULTIMATE_CHARGE, ULTIMATE_CHARGE_MAX, ULTIMATE_COUNT, ULTIMATE_COUNT_MAX,
              SPIRIT_SHADOW, SPIRIT_SHADOW_MAX, HEALTH_MIN, WALK_SPEED, JOG_SPEED, SPRINT_SPEED, CROUCH_SPEED)
BY_NAME = {f.name: f for f in ATTRIBUTES}

MOVEMENT = {f.name: f for f in (WALK_SPEED, JOG_SPEED, SPRINT_SPEED, CROUCH_SPEED)}
INCOMING = {'incoming:physical': TAKEN_PHYSICAL, 'incoming:elemental': TAKEN_ELEMENTAL}
# features written as a single attribute by targets()
RESOURCE_FIELD = {'health': HEALTH, 'shachi': SHACHI, 'attack': ATTACK, 'health_floor': HEALTH_MIN,
                  'spirit_shadow': SPIRIT_SHADOW, 'ultimate_charge': ULTIMATE_CHARGE, 'ultimate_count': ULTIMATE_COUNT}
# maximum checked by refill()
RESOURCE_MAX = {'health': HEALTH_MAX, 'shachi': SHACHI_MAX, 'spirit_shadow': SPIRIT_SHADOW_MAX}
# status() order, kept identical to the read sequence
STATUS_ATTRIBUTES = (('health', HEALTH), ('health_max', HEALTH_MAX), ('health_min', HEALTH_MIN),
                     ('shachi', SHACHI), ('shachi_max', SHACHI_MAX), ('attack', ATTACK))
STATUS_ULTIMATE = (('ultimate_charge', ULTIMATE_CHARGE), ('ultimate_charge_max', ULTIMATE_CHARGE_MAX),
                   ('ultimate_count', ULTIMATE_COUNT), ('ultimate_count_max', ULTIMATE_COUNT_MAX))

# Other reflected classes
PAWN_DUST = Field('Pawn', 'ConsciousnessDust', 0x1040, T_INT, 4)
PAWN_WEAPONS = Field('Pawn', 'Character Weapons Component', 0xec0, T_OBJ, 8)
WEAPONS_RUNTIME_DATA = Field('CharacterWeaponsComponent', 'RuntimeWeaponData', 0xf8, T_MAP, 80)
# NOT verified by reflection yet; kept unchecked on purpose (planned for step 10).
UNVERIFIED_WEAPON_CONFIG_PTR = 0xe8
SWITCH_MAIN_COOLDOWN = Field('WeaponConfig', 'Switch Main Weapon Cooldown', 0x9e8, T_DOUBLE, 8)
SWITCH_SHACHI_COOLDOWN = Field('WeaponConfig', 'Switch Shachi Weapon Cooldown', 0x9f0, T_DOUBLE, 8)
CHANGE_WEAPON_COOLDOWN = Field('WeaponConfig', 'Change Weapon Cooldown', 0xe0, T_DOUBLE, 8)
BACKPACK_MEDICINES = Field('BackPackSubsystem', 'MedicineInfos', 0x620, T_MAP, 80)
BACKPACK_MEDICINE_EXTRA = Field('BackPackSubsystem', 'MedicineExtraUsage', 0x670, T_INT, 4)
MEDICINE_CURRENT = Field('PBZMedicineInfo', 'CurrentTime', 0x50, T_INT, 4)
MEDICINE_DISPLAY = Field('PBZMedicineInfo', 'DisplayTime', 0x54, T_INT, 4)
MEDICINE_MAX = Field('PBZMedicineInfo', 'MaxTime', 0x58, T_INT, 4)
MEDICINE_FIELDS = (MEDICINE_CURRENT, MEDICINE_DISPLAY, MEDICINE_MAX)
# Registered only; teleport_unlock.py keeps its own layout until step 13.
BACKPACK_INVENTORIES = 0x510

# Fixed pointer chain (not reflected)
WORLD_GAME_INSTANCE = 0x228
GI_LOCAL_PLAYERS = 0x38
PLAYER_CONTROLLER = 0x30
CONTROLLER_PAWN = 0x2f0
CONTROLLER_PAWN_CHECK = 0x358
PAWN_ATTRIBUTE_SET = 0x758
WORLD_SUBSYSTEMS = 0x9e0
RF_CLASS_DEFAULT_OBJECT = 0x10

# Business limits
LIMITS = {'resource_max': 1000000, 'ultimate_count_max': 100, 'dust_max': 2000000000, 'medicine_max': 50}
