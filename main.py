import sys
import datetime
import os
import json
import shutil
import argparse
import logging
import itertools
import collections
import copy
import hashlib
from urllib.error import URLError
from urllib.request import urlopen, Request
import xml.etree.ElementTree as ET
from termcolor import colored
import coloredlogs
import splitter
import specdata

try:
    from settings_local import settings
except ImportError:
    from settings import settings

__version__ = "9.0.1"

# Items to parse. First entry is the "correct" name
gear_slots = [("head",),
              ("neck",),
              ("shoulder", "shoulders"),
              ("back",),
              ("chest",),
              ("wrist", "wrists"),
              ("hands",),
              ("waist",),
              ("legs",),
              ("feet",),
              ("finger", "finger1", "finger2"),
              ("trinket", "trinket1", "trinket2",),
              ("main_hand",),
              ("off_hand",)]

gem_ids = {"16haste": 311865,
           "haste": 311865,  # always contains available maximum quality
           "16crit": 311863,
           "crit": 311863,  # always contains available maximum quality
           "16vers": 311859,
           "vers": 311859,  # always contains available maximum quality
           "16mast": 311864,
           "mast": 311864,  # always contains available maximum quality
           }

# Global logger instance
logger = logging.getLogger()
if (logger.hasHandlers()):
    logger.handlers.clear()
logger.setLevel(logging.DEBUG)

log_handler = logging.FileHandler('autosimc.log', encoding='utf-8')
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_handler.setFormatter(formatter)
logger.addHandler(log_handler)

stdout_handler = logging.StreamHandler(sys.stdout)
stdout_handler.setLevel(logging.INFO)
color_formatter = coloredlogs.ColoredFormatter('%(asctime)s - %(levelname)s - %(message)s')
stdout_handler.setFormatter(color_formatter)
logger.addHandler(stdout_handler)


def stable_unique(seq):
    """
    Filter sequence to only contain unique elements, in a stable order
    This is a replacement for x = list(set(x)), which does not lead to
    deterministic or 'stable' output.
    Credit to https://stackoverflow.com/a/480227
    """
    seen = set()
    seen_add = seen.add
    return [x for x in seq if not (x in seen or seen_add(x))]


def get_additional_input():
    input_encoding = 'utf-8'
    options = []
    try:
        with open(additionalFileName, "r", encoding=input_encoding) as f:
            for line in f:
                if not line.startswith("#"):
                    options.append(line)

    except UnicodeDecodeError as e:
        raise RuntimeError("""AutoSimC could not decode your additional input file '{file}' with encoding '{enc}'.
        Please make sure that your text editor encodes the file as '{enc}',
        or as a quick fix remove any special characters from your character name.""".format(file=additionalFileName,
                                                                                            enc=input_encoding)) from e

    return "".join(options)


def build_gem_list(gem_lists):
    """Build list of unique gem ids from --gems argument"""
    sorted_gem_list = []
    for gems in gem_lists:
        splitted_gems = gems.split(",")
        for gem in splitted_gems:
            if gem not in gem_ids.keys():
                raise ValueError(f'Unknown gem "{gem}" to sim, please check your input. Valid gems: {gem_ids.keys()}')
        # Convert parsed gems to list of gem ids
        gems = [gem_ids[gem] for gem in splitted_gems]

        # Unique by gem id, so that if user specifies eg. 200haste,haste there will only be 1 gem added.
        gems = stable_unique(gems)
        sorted_gem_list += gems
    logger.debug(f'Parsed gem list to permutate: {sorted_gem_list}')
    return sorted_gem_list


def str2bool(v):
    return v.lower() in ('yes', 'true', 't', '1', 'y')


def parse_command_line_args():
    """Parse command line arguments using argparse. Also provides --help functionality, and default values for args"""

    parser = argparse.ArgumentParser(prog="AutoSimC",
                                     description=("Python script to create multiple profiles for SimulationCraft to "
                                                  "find Best-in-Slot and best enchants/gems/talents combinations."),
                                     epilog=("Don't hesitate to go on the SimcMinMax Discord "
                                             "(https://discordapp.com/invite/tFR2uvK) "
                                             "in the #simpermut-autosimc Channel to ask about specific stuff."),
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter  # Show default arguments
                                     )

    parser.add_argument('-i', '--inputfile',
                        dest="inputfile",
                        default=settings.default_inputFileName,
                        required=False,
                        help=("Inputfile describing the permutation of SimC profiles to generate. See README for more "
                              "details."))

    parser.add_argument('-o', '--outputfile',
                        dest="outputfile",
                        default=settings.default_outputFileName,
                        required=False,
                        help=("Output file containing the generated profiles used for the simulation."))

    parser.add_argument('-a', '--additionalfile',
                        dest="additionalfile",
                        default=settings.default_additionalFileName,
                        required=False,
                        help=("Additional input file containing the options to add to each profile."))

    parser.add_argument('-sim', '--sim',
                        dest="sim",
                        required=False,
                        nargs=1,
                        default=[settings.default_sim_start_stage],
                        choices=['permutate_only', 'all', 'stage1', 'stage2', 'stage3', 'stage4',
                                 'stage5', 'stage6'],
                        help=("Enables automated simulation and ranking for the top 3 dps-gear-combinations. "
                              "Might take a long time, depending on number of permutations. "
                              "Edit the simcraft-path in settings.py to point to your simc-installation. The result.html "
                              "will be saved in results-subfolder."
                              "There are 2 modes available for calculating the possible huge amount of permutations: "
                              "Static and dynamic mode:"
                              "* Static uses a fixed amount of simc-iterations at the cost of quality; default-settings are "
                              "100, 1000 and 10000 for each stage."
                              "* Dynamic mode lets you set the target_error-parameter from simc, resulting in a more "
                              "accurate ranking. Stage 1 can be entered at the beginning in the wizard. Stage 2 is set to "
                              "target_error=0.2, and 0.05 for the final stage 3."
                              "(These numbers might be changed in future versions)"
                              "You have to set the simc path in the settings.py file."
                              "- Resuming: It is also possible to resume at a stage, e.g. if simc.exe crashed during "
                              "stage1, by launching with the parameter -sim stage1 (or stage2/3)."
                              "- Parallel Processing: By default multiple simc-instances are launched for stage1 and 2, "
                              "which is a major speedup on modern multicore-cpus like AMD Ryzen. If you encounter problems "
                              "or instabilities, edit settings.py and change the corresponding parameters or even disable it.")
                        )

    parser.add_argument('-stages', '--stages',
                        dest="stages",
                        required=False,
                        type=int,
                        default=settings.num_stages,
                        help='Number of stages to simulate.')

    parser.add_argument('-gems', '--gems',
                        dest="gems",
                        required=False,
                        nargs="*",
                        help=('Enables permutation of gem-combinations in your gear. With e.g. gems crit,haste,int '
                              'you can add all combinations of the corresponding gems (epic gems: 200, rare: 150, uncommon '
                              'greens are not supported) in addition to the ones you have currently equipped.\n'
                              'Valid gems: {}'
                              '- Example: You have equipped 1 int and 2 mastery-gems. If you enter <-gems "crit,haste,int"> '
                              '(without <>) into the commandline, the permutation process uses the single int- '
                              'and mastery-gem-combination you have currrently equipped and adds ALL combinations from the '
                              'ones in the commandline, therefore mastery would be excluded. However, adding mastery to the '
                              'commandline reenables that.\n'
                              '- Gems have to fulfil the following syntax in your profile: gem_id=123456[[/234567]/345678] '
                              'Simpermut usually creates this for you.\n'
                              '- WARNING: If you have many items with sockets and/or use a vast gem-combination-setup as '
                              'command, the number of combinations will go through the roof VERY quickly. Please be cautious '
                              'when enabling this.'
                              '- additonally you can specify a empty list of gems, which will permutate the existing gems'
                              'in your input gear.').format(list(gem_ids.keys())))

    parser.add_argument('-unique_jewelry', '--unique_jewelry',
                        dest='unique_jewelry',
                        type=str2bool,
                        default="true",
                        help='Assume ring and trinkets are unique-equipped, and only a single item id can be equipped.')

    parser.add_argument('-version', '--version',
                        action='version', version='%(prog)s {}'.format(__version__))

    parser.add_argument('-scale', '--scale',
                        dest="scale",
                        action='store_true',
                        help='Run scale calcs.')

    return parser.parse_args()


# Manage command line parameters
def handleCommandLine():
    args = parse_command_line_args()

    # Sim stage is always a list with 1 element, eg. ["all"], ['stage1'], ...
    args.sim = args.sim[0]
    if args.sim == 'permutate_only':
        args.sim = None

    # For now, just write command line arguments into globals
    global outputFileName
    outputFileName = args.outputfile

    global additionalFileName
    additionalFileName = args.additionalfile

    global num_stages
    num_stages = args.stages

    global scale
    scale = args.scale

    return args


def cleanup_subdir(subdir):
    if os.path.exists(subdir):
        logger.debug(f'Removing subdir "{subdir}".')
        shutil.rmtree(subdir)


def fetch_from_wowhead(dict, ilvl):
    if not os.path.exists("cache"):
        os.makedirs("cache")

    filename = f'cache/{dict["id"]}.json'
    if os.path.isfile(filename):
        with open(filename, "r") as f:
            json_string = f.read()
        return json_string

    try:
        hdr = {'Accept': 'text/html,application/xhtml+xml,*/*',
               "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/48.0.2564.116 Safari/537.36"}
        url = f'https://www.wowhead.com/item={dict["id"]}&xml'
        if "bonus_id" in dict:
            bonus_id = dict["bonus_id"].replace('/', ':')
            url = url + f'&bonus={bonus_id}'
        if "enchant_id" in dict:
            url = url + f'&ench={dict["enchant_id"]}'
        if ilvl != 0:
            url = url + f'&ilvl={ilvl}'

        req = Request(url, headers=hdr)
        with urlopen(req) as socket:
            xml = socket.read().decode('UTF-8')
        root = ET.fromstring(xml)
        item_json = json.loads('{' + root.find('item/json').text + '}')
        # set the ilvl and quality in the JSON from the XML
        item_json["quality"] = int(root.find('item/quality').attrib["id"])
        item_json["level"] = int(root.find('item/level').text)
        json_string = json.dumps(item_json)
        with open(filename, "w") as f:
            f.write(json_string)
        return json_string
    except URLError as ex:
        logger.warning(f'Could not access download from wowhead {ex.reason}')
        return ""


item_colors = {
    0: lambda x: colored(x.replace('_', ' ').title(), 'grey'),
    1: lambda x: colored(x.replace('_', ' ').title(), 'white'),
    2: lambda x: colored(x.replace('_', ' ').title(), 'green'),
    3: lambda x: colored(x.replace('_', ' ').title(), 'blue'),
    4: lambda x: colored(x.replace('_', ' ').title(), 'magenta'),
    5: lambda x: colored(x.replace('_', ' ').title(), 'yellow')
}

stat_names = {
    "Str": "Strength",
    "Agi": "Agility",
    "Sta": "Stamina",
    "Int": "Intellect",
    "SP": "SpellPower",
    "AP": "Ap",
    "Crit": "CritRating",
    "Haste": "HasteRating",
    "Mastery": "MasteryRating",
    "Vers": "Versatility",
    "Wdps": "Dps",
    "WOHdps": "OffHandDps",
    "Armor": "Armor",
    "Bonusarmor": "BonusArmor",
    "Leech": "Leech",
    "Runspeed": "RunSpeed",
    "Latency": "Latency"
}


def print_best(filename):
    with open(filename) as f:
        results = json.load(f)
    currentBestDps = 0
    currentBestIndex = ""

    for player in results["sim"]["players"]:
        if player["collected_data"]["dpse"]["mean"] > currentBestDps:
            currentBestDps = player["collected_data"]["dpse"]["mean"]
            currentBestIndex = player["name"]

    print(colored(currentBestIndex.upper().split('_')[0], 'green'), colored(f'{currentBestDps:8.0f}', 'red'))

    for player in results["sim"]["players"]:
        if player["name"] == currentBestIndex:
            # print out the gear
            for slot, item in player["gear"].items():
                d = dict(x.split("=") for x in ('name=' + item["encoded_item"]).split(','))

                json_string = fetch_from_wowhead(d, item["ilevel"])
                item_json = json.loads(json_string)

                print(f'{slot.ljust(11).title()} {item_colors[item_json["quality"]](d["name"])}', colored(f'[{item["ilevel"]}]', 'white'))

            if "scale_factors" in player and len(player["scale_factors"]) > 0:
                # print out the Pawn string
                name = player["name"].split('_')[0]
                player_class = player["specialization"].split(' ')[1]
                spec = player["specialization"].split(' ')[0]
                pawn_string = f'(Pawn: v1: \"{name}-{spec}\": Class={player_class}, Spec={spec},'
                for stat, value in player["scale_factors"].items():
                    pawn_string = pawn_string + f'{stat_names[stat]}={value:2.2f}, '
                pawn_string = pawn_string.rstrip(', ') + ')'
                print(f'\nPAWN STRING: {pawn_string}')

            # print out the talents
            print(f'\nTALENTS: {", ".join([str(t["name"]) for t in player["talents"]])}')


def copy_result_file(last_subdir):
    result_folder = os.path.abspath(settings.result_subfolder)
    if not os.path.exists(result_folder):
        logger.debug(("Result-subfolder '{}' does not exist. Creating it.").format(result_folder))
        os.makedirs(result_folder)

    # Copy html files from last subdir to results folder
    found_html = False
    if os.path.exists(last_subdir):
        for _root, _dirs, files in os.walk(last_subdir):
            for file in files:
                if file.endswith(".html") or file.endswith(".json"):
                    src = os.path.join(last_subdir, file)
                    dest = os.path.join(result_folder, file)
                    logger.debug(f'Moving file: {src} to {dest}')
                    shutil.move(src, dest)
                    found_html = True
                    if file.endswith(".json"):
                        print_best(os.path.join(result_folder, file))
    if not found_html:
        logger.warning(f'Could not copy html result file, since there was no file found in "{last_subdir}".')


def cleanup():
    logger.debug('Cleaning up')
    subdirs = [get_subdir(stage) for stage in range(1, num_stages + 1)]
    copy_result_file(subdirs[-1])
    for subdir in subdirs:
        cleanup_subdir(subdir)


def validateSettings(args):
    """Check input arguments and settings.py options"""
    # Check simc executable availability.
    if args.sim:
        if not os.path.exists(os.path.expanduser(settings.simc_path)):
            raise FileNotFoundError(f'Simc executable at "{settings.simc_path}" does not exist.')
        else:
            logger.debug(f'Simc executable at "{settings.simc_path}" does not exist.')

    # use a "safe mode", overwriting the values
    if settings.simc_safe_mode:
        logger.info('Using Safe Mode as specified in settings.')
        settings.simc_threads = 1

    if settings.default_error_rate_multiplier <= 0:
        raise ValueError(f'Invalid default_error_rate_multiplier ({settings.default_error_rate_multiplier}) <= 0')

    valid_grabbing_methods = 'target_error', 'top_n'
    if settings.default_grabbing_method not in valid_grabbing_methods:
        raise ValueError(f'Invalid settings.default_grabbing_method "{settings.default_grabbing_method}"". Valid options: {valid_grabbing_methods}')


def file_checksum(filename):
    h = hashlib.sha256()
    with open(filename, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            h.update(chunk)
    return h.hexdigest()


def get_gem_combinations(gems_to_use, num_gem_slots):
    if num_gem_slots <= 0:
        return []
    combinations = itertools.combinations_with_replacement(gems_to_use, r=num_gem_slots)
    return list(combinations)


def permutate_talents(talents_list):
    talents_list = talents_list.split('|')
    all_talent_combinations = []  # List for each talents input
    for talents in talents_list:
        current_talents = []
        for talent in talents:
            if talent == "0":
                # We permutate the talent row, adding ['1', '2', '3'] to that row
                current_talents.append([str(x) for x in range(1, 4)])
            else:
                # Do not permutate the talent row, just add the talent from the profile
                current_talents.append([talent])
        all_talent_combinations.append(current_talents)
        logger.debug(f'Talent combination input: {current_talents}')

    # Use some itertools magic to unpack the product of all talent combinations
    product = [itertools.product(*t) for t in all_talent_combinations]
    product = list(itertools.chain(*product))

    # Format each permutation back to a nice talent string.
    permuted_talent_strings = ["".join(s) for s in product]
    permuted_talent_strings = stable_unique(permuted_talent_strings)
    logger.debug(f'Talent combinations: {permuted_talent_strings}')
    return permuted_talent_strings


def chop_microseconds(delta):
    """Chop microseconds from a timedelta object"""
    return delta - datetime.timedelta(microseconds=delta.microseconds)


def print_permutation_progress(valid_profiles, current, maximum, start_time, max_profile_chars, progress, max_progress):
    # output status every 5000 permutations, user should get at least a minor progress shown; also does not slow down
    # computation very much
    print_every_n = max(int(50000 / (maximum / max_progress)), 1)
    if progress % print_every_n == 0 or progress == max_progress:
        pct = 100.0 * current / maximum
        elapsed = datetime.datetime.now() - start_time
        bandwith = current / 1000 / elapsed.total_seconds() if elapsed.total_seconds() else 0.0
        bandwith_valid = valid_profiles / 1000 / elapsed.total_seconds() if elapsed.total_seconds() else 0.0
        elapsed = chop_microseconds(elapsed)
        remaining_time = elapsed * (100.0 / pct - 1.0) if current else 'NaN'
        if current > maximum:
            remaining_time = datetime.timedelta(seconds=0)
        if isinstance(remaining_time, datetime.timedelta):
            remaining_time = chop_microseconds(remaining_time)
        valid_pct = 100.0 * valid_profiles / current if current else 0.0
        logger.info("Processed {}/{} ({:5.2f}%) valid {} ({:5.2f}%) elapsed_time {} "
                    "remaining {} bw {:.0f}k/s bw(valid) {:.0f}k/s"
                    .format(str(current).rjust(max_profile_chars),
                            maximum,
                            pct,
                            valid_profiles,
                            valid_pct,
                            elapsed,
                            remaining_time,
                            bandwith,
                            bandwith_valid))


class Profile:
    """Represent global profile data"""


class PermutationData:
    """Data for each permutation"""

    def __init__(self, items, profile, max_profile_chars):
        self.profile = profile
        self.max_profile_chars = max_profile_chars
        self.items = items

    def permutate_gems(self, items, gem_list):
        gems_on_gear = []
        gear_with_gems = {}
        for slot, gear in items.items():
            gems_on_gear += gear.gem_ids
            gear_with_gems[slot] = len(gear.gem_ids)

        logger.debug(f'gems on gear: {gems_on_gear}')
        if len(gems_on_gear) == 0:
            return

        # Combine existing gems of the item with the gems supplied by --gems
        combined_gem_list = gems_on_gear
        combined_gem_list += gem_list
        combined_gem_list = stable_unique(combined_gem_list)
        logger.debug(f'Combined gem list: {combined_gem_list}')
        new_gems = get_gem_combinations(combined_gem_list, len(gems_on_gear))
        logger.debug(f'New Gems: {new_gems}')
        new_combinations = []
        for gems in new_gems:
            new_items = copy.deepcopy(items)
            gems_used = 0
            for _i, (slot, num_gem_slots) in enumerate(gear_with_gems.items()):
                copied_item = copy.deepcopy(new_items[slot])
                copied_item.gem_ids = gems[gems_used:gems_used + num_gem_slots]
                new_items[slot] = copied_item
                gems_used += num_gem_slots
            new_combinations.append(new_items)
            logger.debug('Gem permutations:')
            for i, comb in enumerate(new_combinations):
                logger.debug(f'Combination {i}')
                for slot, item in comb.items():
                    logger.debug(f'{slot}: {item}')
                logger.debug('')
        return new_combinations

    def update_talents(self, talents):
        self.talents = talents

    def get_profile_name(self, valid_profile_number):
        return str(valid_profile_number).rjust(self.max_profile_chars, "0")

    def get_profile(self):
        items = []
        # Hack for now to get Txx and L strings removed from items
        for item in self.items.values():
            items.append(item.output_str)
        return "\n".join(items)

    def write_to_file(self, filehandler, valid_profile_number, additional_options):
        profile_name = self.get_profile_name(valid_profile_number)

        filehandler.write("{}={}\n".format(self.profile.wow_class, str.replace(
            self.profile.profile_name, "\"", "")+"_"+profile_name))
        filehandler.write(self.profile.general_options)
        filehandler.write("\ntalents={}\n".format(self.talents))
        filehandler.write(self.get_profile())
        filehandler.write("\n{}\n".format(additional_options))
        filehandler.write("\n")


def build_profile_simc_addon(args):
    valid_classes = ["priest",
                     "druid",
                     "warrior",
                     "paladin",
                     "hunter",
                     "deathknight",
                     "demonhunter",
                     "mage",
                     "monk",
                     "rogue",
                     "shaman",
                     "warlock",
                     ]
    # Parse general profile options
    simc_profile_options = ["race",
                            "level",
                            "server",
                            "region",
                            "professions",
                            "spec",
                            "role",
                            "talents",
                            "position",
                            "azerite_essences",
                            "covenant",
                            "soulbind",
                            "potion",
                            "flask",
                            "food",
                            "augmentation"]

    # will contain any gear in file for each slot, divided by |
    gear = {}
    for slot in gear_slots:
        gear[slot[0]] = []
    gearInBags = {}
    for slot in gear_slots:
        gearInBags[slot[0]] = []

    # no sections available, so parse each line individually
    input_encoding = 'utf-8'
    c_class = ""
    try:
        with open(args.inputfile, "r", encoding=input_encoding) as f:
            player_profile = Profile()
            player_profile.args = args
            player_profile.simc_options = {}
            for line in f:
                if line == '\n':
                    continue
                if line.startswith('#'):
                    if line.startswith('# bfa.reorigination_array_stacks'):
                        splitted = line.split('=', 1)[1].rstrip().lstrip()
                        player_profile.simc_options["bfa.reorigination_array_stacks"] = splitted
                    if line.startswith('# SimC Addon') or line.startswith('# 8.0 Note:') or line == '' or line == '\n':
                        continue
                    else:
                        # gear-in-bag handling
                        splittedLine = line.replace('#', '').replace('\n', '').lstrip().rstrip().split('=', 1)
                        for gearslot in gear_slots:
                            if splittedLine[0].replace('\n', '') == gearslot[0]:
                                gearInBags[splittedLine[0].replace('\n', '')].append(splittedLine[1].replace('\n', '').lstrip().rstrip())
                            # trinket and finger-handling
                            trinketOrRing = splittedLine[0].replace('\n', '').replace('1', '').replace('2', '')
                            if (trinketOrRing == 'finger' or trinketOrRing == 'trinket') and trinketOrRing == gearslot[0]:
                                gearInBags[splittedLine[0].replace('\n', '').replace('1', '').replace('2', '')].append(splittedLine[1].lstrip().rstrip())
                else:
                    splittedLine = line.split("=", 1)
                    if splittedLine[0].replace('\n', '') in valid_classes:
                        c_class = splittedLine[0].replace('\n', '').lstrip().rstrip()
                        player_profile.wow_class = c_class
                        player_profile.profile_name = splittedLine[1].replace('\n', '').lstrip().rstrip()
                    if splittedLine[0].replace('\n', '') in simc_profile_options:
                        player_profile.simc_options[splittedLine[0].replace('\n', '')] = splittedLine[1].replace('\n', '').lstrip().rstrip()
                    for gearslot in gear_slots:
                        if splittedLine[0].replace('\n', '') == gearslot[0]:
                            gear[splittedLine[0].replace('\n', '')].append(splittedLine[1].replace('\n', '').lstrip().rstrip())
                        # trinket and finger-handling
                        trinketOrRing = splittedLine[0].replace('\n', '').replace('1', '').replace('2', '')
                        if (trinketOrRing == "finger" or trinketOrRing == "trinket") and trinketOrRing == gearslot[0]:
                            gear[splittedLine[0].replace('\n', '').replace('1', '').replace('2', '')].append(splittedLine[1].lstrip().rstrip())

    except UnicodeDecodeError as e:
        raise RuntimeError("""AutoSimC could not decode your input file '{file}' with encoding '{enc}'.
        Please make sure that your text editor encodes the file as '{enc}',
        or as a quick fix remove any special characters from your character name.""".format(file=args.inputfile, enc=input_encoding)) from e
    if c_class != "":
        player_profile.class_spec = specdata.getClassSpec(c_class, player_profile.simc_options["spec"])
        player_profile.class_role = specdata.getRole(c_class, player_profile.simc_options["spec"])

    # Build 'general' profile options which do not permutate once into a simc-string
    logger.info(f'SimC options: {player_profile.simc_options}')
    player_profile.general_options = "\n".join(["{}={}".format(key, value) for key, value in
                                                player_profile.simc_options.items()])
    logger.debug(f'Built simc general options string: {player_profile.general_options}')

    # Parse gear
    player_profile.simc_options["gear"] = gear
    player_profile.simc_options["gearInBag"] = gearInBags

    return player_profile


class Item:
    """WoW Item"""

    def __init__(self, slot, input_string=""):
        self._slot = slot
        self.name = ""
        self.item_id = 0
        self.bonus_ids = []
        self.enchant_ids = []
        self._gem_ids = []
        self.drop_level = 0
        self.extra_options = {}

        if len(input_string):
            self.parse_input(input_string.strip('"'))

        self._build_output_str()  # Pre-Build output string as good as possible

    @property
    def slot(self):
        return self._slot

    @slot.setter
    def slot(self, value):
        self._slot = value
        self._build_output_str()

    @property
    def gem_ids(self):
        return self._gem_ids

    @gem_ids.setter
    def gem_ids(self, value):
        self._gem_ids = value
        self._build_output_str()

    def parse_input(self, input_string):
        parts = input_string.split(',')
        self.name = parts[0]

        splitted_name = self.name.split('--')
        if len(splitted_name) > 1:
            self.name = splitted_name[1]

        for s in parts[1:]:
            name, value = s.split("=")
            name = name.lower()
            if name == 'id':
                self.item_id = int(value)
            elif name == 'bonus_id':
                self.bonus_ids = [int(v) for v in value.split("/")]
            elif name == 'enchant_id':
                self.enchant_ids = [int(v) for v in value.split("/")]
            elif name == 'gem_id':
                self.gem_ids = [int(v) for v in value.split("/")]
            elif name == 'drop_level':
                self.drop_level = int(value)
            else:
                if name not in self.extra_options:
                    self.extra_options[name] = []
                self.extra_options[name].append(value)

    def _build_output_str(self):
        self.output_str = f'{self.slot}={self.name},id={self.item_id}'
        if len(self.bonus_ids):
            self.output_str += ",bonus_id=" + "/".join([str(v) for v in self.bonus_ids])
        if len(self.enchant_ids):
            self.output_str += ",enchant_id=" + "/".join([str(v) for v in self.enchant_ids])
        if len(self.gem_ids):
            self.output_str += ",gem_id=" + "/".join([str(v) for v in self.gem_ids])
        if self.drop_level > 0:
            self.output_str += ",drop_level=" + str(self.drop_level)
        for name, values in self.extra_options.items():
            for value in values:
                self.output_str += f',{name}={value}'

    def __str__(self):
        return "Item({})".format(self.output_str)

    def __repr__(self):
        return self.__str__()

    def __eq__(self, other):
        return self.__str__() == other.__str__()

    def __hash__(self):
        # We are just lazy and use __str__ to avoid all the complexity about having mutable members, etc.
        return hash(str(self.__dict__))


def product(*iterables):
    """
    Custom product function as a generator, instead of itertools.product
    This uses way less memory than itertools.product, because it is a generator only yielding a single item at a time.
    requirement for this is that each iterable can be restarted.
    Thanks to https://stackoverflow.com/a/12094519
    """
    if len(iterables) == 0:
        yield ()
    else:
        it = iterables[0]
        for item in iter(it):
            for items in product(*iterables[1:]):
                yield (item,) + items


def permutate(args, player_profile):
    logger.info('Calculating Permutations...')

    parsed_gear = collections.OrderedDict({})

    gear = player_profile.simc_options.get('gear')
    gearInBags = player_profile.simc_options.get('gearInBag')

    # concatenate gear in bags to normal gear-list
    for gear_in_bag in gearInBags:
        if gear_in_bag in gear:
            if len(gear[gear_in_bag]) > 0:
                currentGear = gear[gear_in_bag][0]
                if gear_in_bag == "finger" or gear_in_bag == "trinket":
                    currentGear = currentGear + "|" + gear[gear_in_bag][1]
                for foundGear in gearInBags.get(gear_in_bag):
                    currentGear = currentGear + '|' + foundGear
                gear[gear_in_bag] = currentGear

    for gear_slot in gear_slots:
        slot_base_name = gear_slot[0]  # First mentioned "correct" item name
        parsed_gear[slot_base_name] = []
        for entry in gear_slot:
            if entry in gear:
                if len(gear[entry]) > 0:
                    for s in gear[entry].split('|'):
                        parsed_gear[slot_base_name].append(
                            Item(slot_base_name, s))
        if len(parsed_gear[slot_base_name]) == 0:
            # We havent found any items for that slot, add empty dummy item
            parsed_gear[slot_base_name] = [Item(slot_base_name, "")]

    logger.debug(f'Parsed gear: {parsed_gear}')

    if args.gems is not None:
        splitted_gems = build_gem_list(args.gems)

    # Filter each slot to only have unique items, before doing any gem permutation.
    for key, value in parsed_gear.items():
        parsed_gear[key] = stable_unique(value)

    # This represents a dict of all options which will be permutated fully with itertools.product
    normal_permutation_options = collections.OrderedDict({})

    # Add talents to permutations
    # l_talents = player_profile.config['Profile'].get("talents", "")
    l_talents = player_profile.simc_options.get('talents')
    talent_permutations = permutate_talents(l_talents)

    # Calculate max number of gem slots in equip. Will be used if we do gem permutations.
    max_gem_slots = 0
    if args.gems is not None:
        for _slot, items in parsed_gear.items():
            max_gem_on_item_slot = 0
            for item in items:
                if len(item.gem_ids) > max_gem_on_item_slot:
                    max_gem_on_item_slot = len(item.gem_ids)
            max_gem_slots += max_gem_on_item_slot

    # no gems on gear so no point calculating gem permutations
    if max_gem_slots == 0:
        args.gems = None

    # Add 'normal' gear to normal permutations, excluding trinket/rings
    gear_normal = {k: v for k, v in parsed_gear.items() if (not k == 'finger' and not k == 'trinket')}
    normal_permutation_options.update(gear_normal)

    # Calculate normal permutations
    normal_permutations = product(*normal_permutation_options.values())
    logger.debug('Building permutations matrix finished.')

    special_permutations_config = {"finger": ("finger1", "finger2"),
                                   "trinket": ("trinket1", "trinket2")
                                   }
    special_permutations = {}
    for name, values in special_permutations_config.items():
        # Get entries from parsed gear, exclude empty finger/trinket lines
        entries = [v for k, v in parsed_gear.items() if k.startswith(name)]
        entries = list(itertools.chain(*entries))

        # Remove empty (id=0) items from trinket/rings, except if there are 0 ring/trinkets specified. Then we need
        # the single dummy item
        remove_empty_entries = [item for item in entries if item.item_id != 0]
        if len(remove_empty_entries):
            entries = remove_empty_entries

        logger.debug(f'Input list for special permutation "{name}": {entries}')
        if args.unique_jewelry:
            # Unique finger/trinkets.
            permutations = itertools.combinations(entries, len(values))
        else:
            permutations = itertools.combinations_with_replacement(entries, len(values))
        permutations = list(permutations)
        for i, (item1, item2) in enumerate(permutations):
            new_item1 = copy.deepcopy(item1)
            new_item1.slot = values[0]
            new_item2 = copy.deepcopy(item2)
            new_item2.slot = values[1]
            permutations[i] = (new_item1, new_item2)

        logger.debug(f'Got {len(permutations)} permutations for {name}.')
        for p in permutations:
            logger.debug(p)

        # Remove equal id's
        if args.unique_jewelry:
            permutations = [p for p in permutations if p[0].item_id != p[1].item_id]
        logger.debug(f'Got {len(permutations)} permutations for {name} after id filter.')
        for p in permutations:
            logger.debug(p)

        # Make unique
        permutations = stable_unique(permutations)
        logger.debug(f'Got {len(permutations)} permutations for {name} after unique filter.')
        for p in permutations:
            logger.debug(p)

        entry_dict = {v: None for v in values}
        special_permutations[name] = [name, entry_dict, permutations]

    # Calculate & Display number of permutations
    max_nperm = 1
    for name, perm in normal_permutation_options.items():
        max_nperm *= len(perm)
    permutations_product = {("normal gear&talents"): "{} ({})".format(max_nperm,
                                                                      {name: len(items) for name, items in
                                                                       normal_permutation_options.items()}
                                                                      )
                            }

    for name, _entries, opt in special_permutations.values():
        max_nperm *= len(opt)
        permutations_product[name] = len(opt)
    max_nperm *= len(talent_permutations)
    gem_perms = 1
    if args.gems is not None:
        max_num_gems = max_gem_slots + len(splitted_gems)
        gem_perms = len(list(itertools.combinations_with_replacement(range(max_gem_slots), max_num_gems)))
        max_nperm *= gem_perms
        permutations_product["gems"] = gem_perms
    permutations_product["talents"] = len(talent_permutations)
    logger.info(f'Max number of normal permutations: {max_nperm}')
    logger.info(f'Number of permutations: {permutations_product}')
    max_profile_chars = len(str(max_nperm))  # String length of max_nperm

    # Get Additional options string
    additional_options = get_additional_input()

    # Start the permutation!
    processed = 0
    progress = 0  # Separate progress variable not counting gem and talent combinations
    max_progress = max_nperm / gem_perms / len(talent_permutations)
    valid_profiles = 0
    start_time = datetime.datetime.now()
    unusable_histogram = {}  # Record not usable reasons
    with open(args.outputfile, 'w') as output_file:
        for perm_normal in normal_permutations:
            for perm_finger in special_permutations["finger"][2]:
                for perm_trinket in special_permutations["trinket"][2]:
                    entries = perm_normal
                    entries += perm_finger
                    entries += perm_trinket
                    items = {e.slot: e for e in entries if isinstance(e, Item)}
                    data = PermutationData(items, player_profile, max_profile_chars)
                    # add gem-permutations to gear
                    if args.gems is not None:
                        gem_permutations = data.permutate_gems(items, splitted_gems)
                    else:
                        gem_permutations = (items,)
                    if gem_permutations is not None:
                        for gem_permutation in gem_permutations:
                            data.items = gem_permutation
                            # Permutate talents after is usable check, since it is independent of the talents
                            for t in talent_permutations:
                                data.update_talents(t)
                                # Additional talent usable check could be inserted here.
                                data.write_to_file(output_file, valid_profiles, additional_options)
                                valid_profiles += 1
                                processed += 1
                    progress += 1
                    print_permutation_progress(valid_profiles, processed, max_nperm, start_time, max_profile_chars, progress, max_progress)

    result = (f'Finished permutations. Valid: {valid_profiles:n} of {processed:n} processed. ({100.0 * valid_profiles / max_nperm if max_nperm else 0.0:.2f}%)')
    logger.info(result)

    # Not usable histogram debug output
    unusable_string = []
    for key, value in unusable_histogram.items():
        unusable_string.append(f'{key:40s}: {value:12b} ({value * 100.0 / max_nperm if max_nperm else 0.0:5.2f}%)')
    if len(unusable_string) > 0:
        logger.info(('Invalid profile statistics: [\n{}]').format("\n".join(unusable_string)))

    # Print checksum so we can check for equality when making changes in the code
    outfile_checksum = file_checksum(args.outputfile)
    logger.info(f'Output file checksum: {outfile_checksum}')

    return valid_profiles


def checkResultFiles(subdir):
    """Check the SimC result files of a previous stage for validity."""
    subdir = os.path.join(os.getcwd(), subdir)

    if not os.path.exists(subdir):
        raise FileNotFoundError(f'Subdir "{subdir}"')

    files = os.listdir(subdir)
    if len(files) == 0:
        raise FileNotFoundError(f'No files in: {subdir}"')

    files = [f for f in files if f.endswith('.result')]
    files = [os.path.join(subdir, f) for f in files]
    for file in files:
        if os.stat(file).st_size <= 0:
            raise RuntimeError(f'Result file "{file}"" is empty.')

    logger.debug(f'{len(files)} valid result files found in {subdir}.')
    logger.info(f'Checked all files in {subdir} : Everything seems to be alright.')


def get_subdir(stage):
    subdir = f'stage_{stage:n}'
    subdir = os.path.join(settings.temporary_folder_basepath, subdir)
    subdir = os.path.abspath(subdir)
    return subdir


def grab_profiles(player_profile, stage):
    """Parse output/result files from previous stage and get number of profiles to simulate"""
    subdir_previous_stage = get_subdir(stage - 1)
    if stage == 1:
        num_generated_profiles = splitter.split(outputFileName, get_subdir(stage), settings.splitting_size, player_profile.wow_class)
    else:
        try:
            checkResultFiles(subdir_previous_stage)
        except Exception as e:
            msg = f'Error while checking result files in {subdir_previous_stage}: {e}. Please restart AutoSimc at a previous stage.'
            raise RuntimeError(msg) from e
        if settings.default_grabbing_method == 'target_error':
            filter_by = 'target_error'
            filter_criterium = None
        elif settings.default_grabbing_method == 'top_n':
            filter_by = 'count'
            filter_criterium = settings.default_top_n[stage - num_stages - 1]
        is_last_stage = (stage == num_stages)
        num_generated_profiles = splitter.grab_best(filter_by, filter_criterium, subdir_previous_stage, get_subdir(stage), outputFileName, not is_last_stage)
    if num_generated_profiles:
        logger.info(f'Found {num_generated_profiles} profile(s) to simulate.')
    return num_generated_profiles


def check_profiles(stage):
    subdir = get_subdir(stage)
    if not os.path.exists(subdir):
        return False
    files = os.listdir(subdir
                       )
    files = [f for f in files if f.endswith(".simc")]
    files = [f for f in files if not f.endswith("arguments.simc")]
    files = [f for f in files if os.stat(os.path.join(subdir, f)).st_size > 0]
    return len(files)


def static_stage(player_profile, stage):
    if stage > num_stages:
        return
    logger.info('----------------------------------------------------')
    logger.info(f'***Entering static mode, STAGE {stage}***')
    num_generated_profiles = grab_profiles(player_profile, stage)
    is_last_stage = (stage == num_stages)
    try:
        num_iterations = settings.default_iterations[stage]
    except Exception:
        num_iterations = None
    if not num_iterations:
        raise ValueError(("Cannot run static mode and skip questions without default iterations set for stage {}.").format(stage))
    splitter.simulate(get_subdir(stage), "iterations", num_iterations, player_profile, stage, is_last_stage, num_generated_profiles, scale)
    static_stage(player_profile, stage + 1)


def dynamic_stage(player_profile, num_generated_profiles, previous_target_error=None, stage=1):
    if stage > num_stages:
        return
    logger.info('----------------------------------------------------')
    logger.info(f"Entering dynamic mode, STAGE {stage}")

    num_generated_profiles = grab_profiles(player_profile, stage)

    try:
        target_error = float(settings.default_target_error[stage])
    except Exception:
        target_error = None

    # If we do not have a target_error in settings, get target_error from user input
    if target_error is None:
        raise ValueError(f"Cannot run dynamic mode without default target_error set for stage {stage}.")

    # if the user chose a target_error which is higher than one chosen in the previous stage
    # he is given an option to adjust it.
    if previous_target_error is not None and previous_target_error <= target_error:
        logger.warning(f'Warning Target_Error chosen in stage {stage - 1}: {previous_target_error} <= Default_Target_Error for stage {stage}: {target_error}')
    is_last_stage = (stage == num_stages)
    splitter.simulate(get_subdir(stage), "target_error", target_error, player_profile, stage, is_last_stage, num_generated_profiles, scale)
    dynamic_stage(player_profile, num_generated_profiles, target_error, stage + 1)


def start_stage(player_profile, num_generated_profiles, stage):
    logger.info('----------------------------------------------------')
    logger.info(f'Starting at stage {stage}')
    logger.info(f'You selected grabbing method "{settings.default_grabbing_method}".')
    mode_choice = int(settings.auto_choose_static_or_dynamic)
    valid_modes = (1, 2)
    if mode_choice not in valid_modes:
        raise RuntimeError(f'Invalid simulation mode "{mode_choice}" selected. Valid modes: {valid_modes}.')
    if mode_choice == 1:
        static_stage(player_profile, stage)
    elif mode_choice == 2:
        dynamic_stage(player_profile, num_generated_profiles, None, stage)
    else:
        assert False


def check_interpreter():
    """Check interpreter for minimum requirements."""
    # Does not really work in practice, since formatted string literals (3.6) lead to SyntaxError prior to execution of
    # the program with older interpreters.
    required_major, required_minor = (3, 5)
    major, minor, _micro, _releaselevel, _serial = sys.version_info
    if major > required_major:
        return
    elif major == required_major:
        if minor >= required_minor:
            return
    raise RuntimeError(("Python-Version too old! You are running Python {}. Please install at least "
                        "Python-Version {}.{}.x").format(sys.version,
                                                         required_major,
                                                         required_minor))


def addFightStyle(profile):
    filepath = os.path.join(os.getcwd(), settings.file_fightstyle)
    filepath = os.path.abspath(filepath)
    logger.debug(f'Opening fight types data file at "{filepath}".')
    with open(filepath, encoding="utf-8") as file:
        try:
            profile.fightstyle = None
            fights = json.load(file)
            if len(fights) > 0:
                # fetch default_profile
                for f in fights:
                    if f["name"] == settings.default_fightstyle:
                        profile.fightstyle = f  # add the whole json-object, files will get created later
                if profile.fightstyle is None:
                    raise ValueError(f'No fightstyle found in .json with name: {settings.default_fightstyle}, exiting.')
            else:
                raise RuntimeError("Did not find entries in fight_style.json.")
        except json.decoder.JSONDecodeError as error:
            logger.error(f"Error while decoding JSON file: {error})", exc_info=True)
            sys.exit(1)

    assert profile.fightstyle is not None
    logger.info(f'Found fightstyle >> >{profile.fightstyle["name"]} << < in {settings.file_fightstyle}')

    return profile


########################
#     Program Start    #
########################


def main():
    global class_spec

    # check version of python-interpreter running the script
    check_interpreter()

    logger.info(f'AutoSimC - Supported WoW-Version: {__version__}')

    args = handleCommandLine()

    logger.debug(f'Parsed command line arguments: {args}')
    logger.debug(f'Parsed settings: {vars(settings)}')

    validateSettings(args)

    player_profile = build_profile_simc_addon(args)

    # can always be rerun since it is now deterministic
    outputGenerated = False
    num_generated_profiles = None
    if args.sim == 'all' or args.sim is None:
        start = datetime.datetime.now()
        num_generated_profiles = permutate(args, player_profile)
        logger.debug(f'Permutating took {datetime.datetime.now() - start}.')
        outputGenerated = True
    elif args.sim == 'stage1':
        num_generated_profiles = permutate(args, player_profile)
        outputGenerated = True

    if outputGenerated:
        if num_generated_profiles == 0:
            raise RuntimeError(('No valid profile combinations found.'
                                ' Please check the "Invalid profile statistics" output and adjust your'
                                ' input.txt and settings.py.'))
        if args.sim:
            if num_generated_profiles and num_generated_profiles > 50000:
                logger.warning('Beware: Computation with Simcraft might take a VERY long time with this amount of profiles!')

    if args.sim:
        player_profile = addFightStyle(player_profile)
        if args.sim == 'stage1' or args.sim == 'all':
            start_stage(player_profile, num_generated_profiles, 1)
        if args.sim == 'stage2':
            start_stage(player_profile, None, 2)
        if args.sim == 'stage3':
            start_stage(player_profile, None, 3)

        if settings.clean_up:
            cleanup()
    logger.info('AutoSimC finished correctly.')


if __name__ == "__main__":
    try:
        main()
        logging.shutdown()
    except Exception as e:
        logger.error(f'Error: {e}', exc_info=True)
        sys.exit(1)
