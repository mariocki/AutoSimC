import sys
import datetime
import os
import json
import shutil
import argparse
import logging
from urllib.error import URLError
from urllib.request import urlopen, Request
import xml.etree.ElementTree as ET
from profile import Profile
from termcolor import colored
import coloredlogs
from permutator import Permutator
import splitter
import specdata
from staticdata import gear_slots, gem_ids

try:
    from settings_local import settings
except ImportError:
    from settings import settings

__version__ = "9.0.1"

OUTPUT_FILENAME = settings.default_outputFileName
ADDITIONAL_FILENAME = settings.default_additionalFileName
NUM_STAGES = settings.num_stages
SCALE = settings.simc_scale_factors_last_stage

# Global logger instance
logger = logging.getLogger()
if logger.hasHandlers():
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


def str2bool(value):
    return value.lower() in ('yes', 'true', 't', '1', 'y')


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
def handle_command_line():
    args = parse_command_line_args()

    # Sim stage is always a list with 1 element, eg. ["all"], ['stage1'], ...
    args.sim = args.sim[0]
    if args.sim == 'permutate_only':
        args.sim = None

    # override the globals with th arg values
    global OUTPUT_FILENAME
    OUTPUT_FILENAME = args.outputfile

    global ADDITIONAL_FILENAME
    ADDITIONAL_FILENAME = args.additionalfile

    global NUM_STAGES
    NUM_STAGES = args.stages

    global SCALE
    SCALE = args.scale

    return args


def cleanup_subdir(subdir):
    if os.path.exists(subdir):
        logger.debug(f'Removing subdir "{subdir}".')
        shutil.rmtree(subdir)


def fetch_from_wowhead(item_details, ilvl):
    if not os.path.exists("cache"):
        os.makedirs("cache")

    filename = f'cache/{item_details["id"]}.json'
    if os.path.isfile(filename):
        with open(filename, "r") as file_pointer:
            json_string = file_pointer.read()
        return json_string

    try:
        hdr = {'Accept': 'text/html,application/xhtml+xml,*/*',
               "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/48.0.2564.116 Safari/537.36"}
        url = f'https://www.wowhead.com/item={item_details["id"]}&xml'
        if "bonus_id" in item_details:
            bonus_id = item_details["bonus_id"].replace('/', ':')
            url = url + f'&bonus={bonus_id}'
        if "enchant_id" in item_details:
            url = url + f'&ench={item_details["enchant_id"]}'
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
        with open(filename, "w") as file_pointer:
            file_pointer.write(json_string)
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
    with open(filename) as file_pointer:
        results = json.load(file_pointer)
    current_best_dps = 0
    current_best_index = ""

    for player in results["sim"]["players"]:
        if player["collected_data"]["dpse"]["mean"] > current_best_dps:
            current_best_dps = player["collected_data"]["dpse"]["mean"]
            current_best_index = player["name"]

    print(colored(current_best_index.upper().split('_')[0], 'green'), colored(f'{current_best_dps:8.0f}', 'red'))

    for player in results["sim"]["players"]:
        if player["name"] == current_best_index:
            # print out the gear
            for slot, item in player["gear"].items():
                item_details = dict(x.split("=") for x in ('name=' + item["encoded_item"]).split(','))

                json_string = fetch_from_wowhead(item_details, item["ilevel"])
                item_json = json.loads(json_string)

                print(f'{slot.ljust(11).title()} {item_colors[item_json["quality"]](item["name"])}', colored(f'[{item["ilevel"]}]', 'white'))

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
    subdirs = [get_subdir(stage) for stage in range(1, NUM_STAGES + 1)]
    copy_result_file(subdirs[-1])
    for subdir in subdirs:
        cleanup_subdir(subdir)


def validate_settings(args):
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
    gear_in_bags = {}
    for slot in gear_slots:
        gear_in_bags[slot[0]] = []

    # no sections available, so parse each line individually
    input_encoding = 'utf-8'
    c_class = ""
    try:
        with open(args.inputfile, "r", encoding=input_encoding) as file_pointer:
            player_profile = Profile()
            player_profile.args = args
            player_profile.simc_options = {}
            for line in file_pointer:
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
                        split_line = line.replace('#', '').replace('\n', '').lstrip().rstrip().split('=', 1)
                        for gearslot in gear_slots:
                            if split_line[0].replace('\n', '') == gearslot[0]:
                                gear_in_bags[split_line[0].replace('\n', '')].append(split_line[1].replace('\n', '').lstrip().rstrip())
                            # trinket and finger-handling
                            trinket_or_ring = split_line[0].replace('\n', '').replace('1', '').replace('2', '')
                            if (trinket_or_ring == 'finger' or trinket_or_ring == 'trinket') and trinket_or_ring == gearslot[0]:
                                gear_in_bags[split_line[0].replace('\n', '').replace('1', '').replace('2', '')].append(split_line[1].lstrip().rstrip())
                else:
                    split_line = line.split("=", 1)
                    if split_line[0].replace('\n', '') in valid_classes:
                        c_class = split_line[0].replace('\n', '').lstrip().rstrip()
                        player_profile.wow_class = c_class
                        player_profile.profile_name = split_line[1].replace('\n', '').lstrip().rstrip()
                    if split_line[0].replace('\n', '') in simc_profile_options:
                        player_profile.simc_options[split_line[0].replace('\n', '')] = split_line[1].replace('\n', '').lstrip().rstrip()
                    for gearslot in gear_slots:
                        if split_line[0].replace('\n', '') == gearslot[0]:
                            gear[split_line[0].replace('\n', '')].append(split_line[1].replace('\n', '').lstrip().rstrip())
                        # trinket and finger-handling
                        trinket_or_ring = split_line[0].replace('\n', '').replace('1', '').replace('2', '')
                        if (trinket_or_ring == 'finger' or trinket_or_ring == 'trinket') and trinket_or_ring == gearslot[0]:
                            gear[split_line[0].replace('\n', '').replace('1', '').replace('2', '')].append(split_line[1].lstrip().rstrip())

    except UnicodeDecodeError as ex:
        raise RuntimeError("""AutoSimC could not decode your input file '{file}' with encoding '{enc}'.
        Please make sure that your text editor encodes the file as '{enc}',
        or as a quick fix remove any special characters from your character name.""".format(file=args.inputfile, enc=input_encoding)) from ex

    if c_class != '':
        player_profile.class_spec = specdata.getClassSpec(c_class, player_profile.simc_options["spec"])
        player_profile.class_role = specdata.getRole(c_class, player_profile.simc_options["spec"])

    # Build 'general' profile options which do not permutate once into a simc-string
    logger.info(f'SimC options: {player_profile.simc_options}')
    player_profile.general_options = "\n".join(["{}={}".format(key, value) for key, value in
                                               player_profile.simc_options.items()])
    logger.debug(f'Built simc general options string: {player_profile.general_options}')

    # Parse gear
    player_profile.simc_options["gear"] = gear
    player_profile.simc_options["gearInBag"] = gear_in_bags

    return player_profile


def check_results_file(subdir):
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
            logger.warning(f'Result file "{file}"" is empty.')

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
        num_generated_profiles = splitter.split(OUTPUT_FILENAME, get_subdir(stage), settings.splitting_size, player_profile.wow_class)
    else:
        try:
            check_results_file(subdir_previous_stage)
        except Exception as ex:
            msg = f'Error while checking result files in {subdir_previous_stage}: {ex}. Please restart AutoSimc at a previous stage.'
            raise RuntimeError(msg) from ex
        if settings.default_grabbing_method == 'target_error':
            filter_by = 'target_error'
            filter_criterium = None
        elif settings.default_grabbing_method == 'top_n':
            filter_by = 'count'
            filter_criterium = settings.default_top_n[stage - NUM_STAGES - 1]
        is_last_stage = (stage == NUM_STAGES)
        num_generated_profiles = splitter.grab_best(filter_by, filter_criterium, subdir_previous_stage, get_subdir(stage), OUTPUT_FILENAME, not is_last_stage)
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
    if stage > NUM_STAGES:
        return
    logger.info('----------------------------------------------------')
    logger.info(f'***Entering static mode, STAGE {stage}***')
    is_last_stage = (stage == NUM_STAGES)
    try:
        num_iterations = settings.default_iterations[stage]
    except Exception:
        num_iterations = None
    if not num_iterations:
        raise ValueError(("Cannot run static mode and skip questions without default iterations set for stage {}.").format(stage))
    splitter.simulate(get_subdir(stage), "iterations", num_iterations, player_profile, stage, is_last_stage, SCALE)
    static_stage(player_profile, stage + 1)


def dynamic_stage(player_profile, num_generated_profiles, previous_target_error=None, stage=1):
    if stage > NUM_STAGES:
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
    is_last_stage = (stage == NUM_STAGES)
    splitter.simulate(get_subdir(stage), "target_error", target_error, player_profile, stage, is_last_stage, SCALE)
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


def add_fight_style(profile):
    filepath = os.path.join(os.getcwd(), settings.file_fightstyle)
    filepath = os.path.abspath(filepath)
    logger.debug(f'Opening fight types data file at "{filepath}".')
    with open(filepath, encoding="utf-8") as file:
        try:
            profile.fightstyle = None
            fights = json.load(file)
            if len(fights) > 0:
                # fetch default_profile
                for fight in fights:
                    if fight["name"] == settings.default_fightstyle:
                        profile.fightstyle = fight  # add the whole json-object, files will get created later
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
    # check version of python-interpreter running the script
    check_interpreter()

    logger.info(f'AutoSimC - Supported WoW-Version: {__version__}')

    args = handle_command_line()

    logger.debug(f'Parsed command line arguments: {args}')
    logger.debug(f'Parsed settings: {vars(settings)}')

    validate_settings(args)

    player_profile = build_profile_simc_addon(args)

    # can always be rerun since it is now deterministic
    output_generated = False
    num_generated_profiles = None
    permutator = Permutator(ADDITIONAL_FILENAME, logger, player_profile, args.gems, args.unique_jewelry, args.outputfile)
    if args.sim == 'all' or args.sim is None:
        start = datetime.datetime.now()
        num_generated_profiles = permutator.permutate()
        logger.debug(f'Permutating took {datetime.datetime.now() - start}.')
        output_generated = True
    elif args.sim == 'stage1':
        num_generated_profiles = permutator.permutate()
        output_generated = True

    if output_generated:
        if num_generated_profiles == 0:
            raise RuntimeError(('No valid profile combinations found.'
                                ' Please check the "Invalid profile statistics" output and adjust your'
                                ' input.txt and settings.py.'))
        if args.sim:
            if num_generated_profiles and num_generated_profiles > 50000:
                logger.warning('Beware: Computation with Simcraft might take a VERY long time with this amount of profiles!')

    if args.sim:
        player_profile = add_fight_style(player_profile)
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
    except Exception as ex:
        logger.error(f'Error: {ex}', exc_info=True)
        sys.exit(1)
