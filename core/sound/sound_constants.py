"""
Shared constants for the GBA M4A sound engine.

Derived from MPlayDef.s and music_voice.inc in the pokefirered decomp.
These mirror the assembler equates so our Python parsers can interpret
the .s song files and voice_groups.inc without a real assembler.
"""

# ---------------------------------------------------------------------------
# Wait durations: Wxx name -> tick count
# The bytecodes are 0x80+index, but we parse from text, so we just need
# the name-to-tick mapping.  W00=0 ticks, W01=1, ... W24=24, then jumps.
# ---------------------------------------------------------------------------
WAIT_TICKS = {
    'W00': 0, 'W01': 1, 'W02': 2, 'W03': 3, 'W04': 4,
    'W05': 5, 'W06': 6, 'W07': 7, 'W08': 8, 'W09': 9,
    'W10': 10, 'W11': 11, 'W12': 12, 'W13': 13, 'W14': 14,
    'W15': 15, 'W16': 16, 'W17': 17, 'W18': 18, 'W19': 19,
    'W20': 20, 'W21': 21, 'W22': 22, 'W23': 23, 'W24': 24,
    'W28': 28, 'W30': 30, 'W32': 32, 'W36': 36, 'W40': 40,
    'W42': 42, 'W44': 44, 'W48': 48, 'W52': 52, 'W54': 54,
    'W56': 56, 'W60': 60, 'W64': 64, 'W66': 66, 'W68': 68,
    'W72': 72, 'W76': 76, 'W78': 78, 'W80': 80, 'W84': 84,
    'W88': 88, 'W90': 90, 'W92': 92, 'W96': 96,
}

# ---------------------------------------------------------------------------
# Note durations: Nxx name -> tick count  (same pattern as waits)
# ---------------------------------------------------------------------------
NOTE_TICKS = {
    'N01': 1, 'N02': 2, 'N03': 3, 'N04': 4, 'N05': 5,
    'N06': 6, 'N07': 7, 'N08': 8, 'N09': 9, 'N10': 10,
    'N11': 11, 'N12': 12, 'N13': 13, 'N14': 14, 'N15': 15,
    'N16': 16, 'N17': 17, 'N18': 18, 'N19': 19, 'N20': 20,
    'N21': 21, 'N22': 22, 'N23': 23, 'N24': 24,
    'N28': 28, 'N30': 30, 'N32': 32, 'N36': 36, 'N40': 40,
    'N42': 42, 'N44': 44, 'N48': 48, 'N52': 52, 'N54': 54,
    'N56': 56, 'N60': 60, 'N64': 64, 'N66': 66, 'N68': 68,
    'N72': 72, 'N76': 76, 'N78': 78, 'N80': 80, 'N84': 84,
    'N88': 88, 'N90': 90, 'N92': 92, 'N96': 96,
}

# ---------------------------------------------------------------------------
# Note names: pitch name -> MIDI note number (0-127)
# From MPlayDef.s: CnM2=0 .. Gn8=127
# ---------------------------------------------------------------------------
NOTE_NAMES: dict[str, int] = {}

_NOTE_LETTERS = ['Cn', 'Cs', 'Dn', 'Ds', 'En', 'Fn', 'Fs', 'Gn', 'Gs', 'An', 'As', 'Bn']

# Octaves M2 and M1 (negative octaves)
for i, letter in enumerate(_NOTE_LETTERS):
    NOTE_NAMES[f'{letter}M2'] = i          # CnM2=0 .. BnM2=11
    NOTE_NAMES[f'{letter}M1'] = 12 + i     # CnM1=12 .. BnM1=23

# Octaves 0-7
for octave in range(8):
    for i, letter in enumerate(_NOTE_LETTERS):
        midi = 24 + octave * 12 + i
        if midi <= 127:
            NOTE_NAMES[f'{letter}{octave}'] = midi

# Octave 8 (partial: Cn8=120 .. Gn8=127)
for i, letter in enumerate(_NOTE_LETTERS[:8]):  # Cn through Gn only
    NOTE_NAMES[f'{letter}8'] = 120 + i

# Reverse lookup: MIDI number -> preferred note name
MIDI_TO_NAME: dict[int, str] = {v: k for k, v in NOTE_NAMES.items()}

# ---------------------------------------------------------------------------
# Control commands (bytecodes, for reference and parsing)
# ---------------------------------------------------------------------------
CMD_FINE  = 0xB1
CMD_GOTO  = 0xB2
CMD_PATT  = 0xB3
CMD_PEND  = 0xB4
CMD_REPT  = 0xB5
CMD_MEMACC = 0xB9
CMD_PRIO  = 0xBA
CMD_TEMPO = 0xBB
CMD_KEYSH = 0xBC
CMD_VOICE = 0xBD
CMD_VOL   = 0xBE
CMD_PAN   = 0xBF
CMD_BEND  = 0xC0
CMD_BENDR = 0xC1
CMD_LFOS  = 0xC2
CMD_LFODL = 0xC3
CMD_MOD   = 0xC4
CMD_MODT  = 0xC5
CMD_TUNE  = 0xC8
CMD_XCMD  = 0xCD
CMD_EOT   = 0xCE
CMD_TIE   = 0xCF

# Text names used in .s files -> our command identifiers
COMMAND_NAMES = {
    'FINE': 'FINE', 'GOTO': 'GOTO', 'PATT': 'PATT', 'PEND': 'PEND',
    'REPT': 'REPT', 'MEMACC': 'MEMACC', 'PRIO': 'PRIO',
    'TEMPO': 'TEMPO', 'KEYSH': 'KEYSH', 'VOICE': 'VOICE',
    'VOL': 'VOL', 'PAN': 'PAN', 'PAM': 'PAN',  # PAM is alias for PAN
    'BEND': 'BEND', 'BENDR': 'BENDR', 'LFOS': 'LFOS', 'LFODL': 'LFODL',
    'MOD': 'MOD', 'MODT': 'MODT', 'TUNE': 'TUNE',
    'XCMD': 'XCMD', 'EOT': 'EOT', 'TIE': 'TIE',
}

# Commands that take a .word (4-byte pointer) argument on the next line
POINTER_COMMANDS = {'GOTO', 'PATT'}

# ---------------------------------------------------------------------------
# Special assembler constants used in expressions
# ---------------------------------------------------------------------------
REVERB_SET = 0x80  # reverb_set = 0x80
MXV = 0x7F         # mxv = 127 (max volume)
C_V = 0x40         # c_v = 64 (center value for PAN/BEND/TUNE)

# ---------------------------------------------------------------------------
# Voice types (from music_voice.inc)
# ---------------------------------------------------------------------------
VOICE_DIRECTSOUND           = 0x00
VOICE_SQUARE_1              = 0x01
VOICE_SQUARE_2              = 0x02
VOICE_PROGRAMMABLE_WAVE     = 0x03
VOICE_NOISE                 = 0x04
VOICE_DIRECTSOUND_NO_RESAMPLE = 0x08
VOICE_SQUARE_1_ALT          = 0x09
VOICE_SQUARE_2_ALT          = 0x0A
VOICE_PROGRAMMABLE_WAVE_ALT = 0x0B
VOICE_NOISE_ALT             = 0x0C
VOICE_DIRECTSOUND_ALT       = 0x10
VOICE_CRY                   = 0x20
VOICE_CRY_REVERSE           = 0x30
VOICE_KEYSPLIT              = 0x40
VOICE_KEYSPLIT_ALL          = 0x80

# Macro name -> voice type byte
VOICE_MACRO_TYPES = {
    'voice_directsound':              VOICE_DIRECTSOUND,
    'voice_directsound_no_resample':  VOICE_DIRECTSOUND_NO_RESAMPLE,
    'voice_directsound_alt':          VOICE_DIRECTSOUND_ALT,
    'voice_square_1':                 VOICE_SQUARE_1,
    'voice_square_1_alt':             VOICE_SQUARE_1_ALT,
    'voice_square_2':                 VOICE_SQUARE_2,
    'voice_square_2_alt':             VOICE_SQUARE_2_ALT,
    'voice_programmable_wave':        VOICE_PROGRAMMABLE_WAVE,
    'voice_programmable_wave_alt':    VOICE_PROGRAMMABLE_WAVE_ALT,
    'voice_noise':                    VOICE_NOISE,
    'voice_noise_alt':                VOICE_NOISE_ALT,
    'voice_keysplit':                 VOICE_KEYSPLIT,
    'voice_keysplit_all':             VOICE_KEYSPLIT_ALL,
}

# Which voice macros reference a DirectSoundWaveData sample
DIRECTSOUND_VOICE_TYPES = {
    'voice_directsound', 'voice_directsound_no_resample', 'voice_directsound_alt',
}

# Which voice macros reference a ProgrammableWaveData sample
PROGRAMMABLE_WAVE_VOICE_TYPES = {
    'voice_programmable_wave', 'voice_programmable_wave_alt',
}

# Modulation types
MOD_VIBRATO = 0   # mod_vib
MOD_TREMOLO = 1   # mod_tre
MOD_AUTOPAN = 2   # mod_pan

# Gate time parameters
GTP1 = 1
GTP2 = 2
GTP3 = 3

# ---------------------------------------------------------------------------
# Music player indices (from music_player_table.inc)
# ---------------------------------------------------------------------------
PLAYER_BGM = 0
PLAYER_SE1 = 1
PLAYER_SE2 = 2
PLAYER_SE3 = 3

PLAYER_NAMES = {0: 'BGM', 1: 'SE1', 2: 'SE2', 3: 'SE3'}
