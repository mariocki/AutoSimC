# pylint: disable=C0103
# pylint: disable=C0301

import sys
import datetime
import os
import json
import shutil
import argparse
import logging
from urllib.error import URLError
from urllib.request import urlopen, urlretrieve
import platform
from enum import Enum, auto

import AddonImporter
import locale

from settings import settings

try:
    from settings_local import settings
except ImportError:
    from settings import settings

__version__ = "9.1.0"

import gettext

gettext.install('AutoSimC')
translator = gettext.translation('AutoSimC', fallback=True)

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

shadowlands_legendary_ids = [171412, 171413, 171414, 171415, 171416, 171417, 171418, 171419,            #plate
                             172314, 172315, 172316, 172317, 172318, 172319, 172320, 172321,            #leather
                             172322, 172323, 172324, 172325, 172326, 172327, 172328, 172329,            #mail
                             173241, 173242, 173243, 173244, 173245, 173246, 173247, 173248, 173249,    #cloth
                             178926, 178927                                                             #ring, neck
                             ]

class WeaponType(Enum):
    DUMMY = -1
    ONEHAND = 13
    SHIELD = 14
    BOW = 15
    TWOHAND = 17
    OFFHAND_WEAPON = 21
    OFFHAND_SPECIAL_WEAPON = 22
    OFFHAND = 23
    GUN = 26


class TranslatedText(str):
    """Represents a translatable text string, while also keeping a reference to the original (englisch) string"""

    def __new__(cls, message, translate=True):
        if translate:
            return super(TranslatedText, cls).__new__(cls, translator.gettext(message))
        else:
            return super(TranslatedText, cls).__new__(cls, message)

    def __init__(self, message, translate=True):
        self.original_message = message

    def format(self, *args, **kwargs):
        s = TranslatedText(str.format(self, *args, **kwargs), translate=False)
        s.original_message = str.format(self.original_message, *args, **kwargs)
        return s


_ = TranslatedText


def install_translation():
    # Based on: (1) https://docs.python.org/3/library/gettext.html
    # (2) https://inventwithpython.com/blog/2014/12/20/translate-your-python-3-program-with-the-gettext-module/
    # Also see Readme.md#Localization for more info
    if settings.localization_language == "auto":
        # get the default locale using the locale module
        default_lang, _default_enc = locale.getdefaultlocale()
    else:
        default_lang = settings.localization_language
    try:
        if default_lang is not None:
            default_lang = [default_lang]
        lang = gettext.translation('AutoSimC', localedir='locale', languages=default_lang)
        lang.install()
        global translator
        translator = lang
    except FileNotFoundError:
        print("No translation for {} available.".format(default_lang))


install_translation()

# Var init with default value
t27min = int(settings.default_equip_t27_min)
t27max = int(settings.default_equip_t27_max)

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
                raise ValueError("Unknown gem '{}' to sim, please check your input. Valid gems: {}".
                                 format(gem, gem_ids.keys()))
        # Convert parsed gems to list of gem ids
        gems = [gem_ids[gem] for gem in splitted_gems]

        # Unique by gem id, so that if user specifies eg. 200haste,haste there will only be 1 gem added.
        gems = stable_unique(gems)
        sorted_gem_list += gems
    logging.debug("Parsed gem list to permutate: {}".format(sorted_gem_list))
    return sorted_gem_list


def str2bool(v):
    return v.lower() in ("yes", "true", "t", "1")


def parse_command_line_args():
    """Parse command line arguments using argparse. Also provides --help functionality, and default values for args"""

    parser = argparse.ArgumentParser(prog="AutoSimC",
                                     description=("Python script to create multiple profiles for SimulationCraft to find Best-in-Slot and best enchants/gems/talents combinations."),
                                     epilog=("Don't hesitate to go on the SimcMinMax Discord (https://discordapp.com/invite/tFR2uvK) in the #simpermut-autosimc Channel to ask about specific stuff."),
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument('-i', '--inputfile',
                        dest="inputfile",
                        default=settings.default_inputFileName,
                        required=False,
                        help=("Inputfile describing the permutation of SimC profiles to generate. See README for more details."))

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
                        choices=['permutate_only', 'all', 'stage1', 'stage2', 'stage3', 'stage4', 'stage5', 'stage6'],
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
                              "or instabilities, edit settings.py and change the corresponding parameters or even disable it."))

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

    args = parser.parse_args()

    # Sim stage is always a list with 1 element, eg. ["all"], ['stage1'], ...
    args.sim = args.sim[0]
    if args.sim == 'permutate_only':
        args.sim = None

    return args


def get_analyzer_data(class_spec):
    """
    Get precomputed analysis data (target_error, iterations, elapsed_time_seconds) for a given class_spec
    """
    result = []
    filename = os.path.join(os.getcwd(), settings.analyzer_path, settings.analyzer_filename)
    with open(filename, "r") as f:
        file = json.load(f)
        for variant in file[0]:
            for p in variant["playerdata"]:
                if p["specialization"] == class_spec:
                    for s in range(len(p["specdata"])):
                        item = (float(variant["target_error"]),
                                int(p["specdata"][s]["iterations"]),
                                float(p["specdata"][s]["elapsed_time_seconds"])
                                )
                        result.append(item)
    return result


def determineSimcVersionOnDisc():
    """gets the version of our simc installation on disc"""
    try:
        p = subprocess.run([settings.simc_path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        match = None
        for raw_line in p.stdout.decode():
            decoded_line = raw_line
            try:
                # git build <branch> <git-ref>
                match = re.search(r'git build \S* (\S+)\)', decoded_line).group(1)
                if match:
                    logging.debug(_("Found program in {}: Git_Version: {}")
                                  .format(settings.simc_path,
                                          match))
                    return match
            except AttributeError:
                # should only contain other lines from simc_standard-output
                pass
        if match is None:
            logging.info(_("Found no git-string in simc.exe, self-compiled?"))
    except FileNotFoundError:
        logging.info(_("Did not find program in '{}'.").format(settings.simc_path))


def determineLatestSimcVersion():
    """gets the version of the latest binaries available on the net"""
    try:
        html = urlopen('http://downloads.simulationcraft.org/nightly/?C=M;O=D').read().decode('utf-8')
    except URLError:
        logging.info("Could not access download directory on simulationcraft.org")
    # filename = re.search(r'<a href="(simc.+win64.+7z)">', html).group(1)
    filename = list(filter(None, re.findall(r'.+nonetwork.+|<a href="(simc.+win64.+7z)">', html)))[0]
    head, _tail = os.path.splitext(filename)
    latest_git_version = head.split("-")[-1]
    logging.debug(_("Latest version available: {}").format(latest_git_version))

    if not len(latest_git_version):
        logging.info(_("Found no git-string in filename, new or changed format?"))

    return (filename, latest_git_version)


def fetch_from_wowhead(item_details, ilvl):
    if not os.path.exists("cache"):
        os.makedirs("cache")

    filename = f'cache/{item_details["id"]}.json'
    if os.path.isfile(filename):
        with open(filename, "r") as file_pointer:
            json_string = file_pointer.read()
        return json_string

    try:
        html = urlopen('http://downloads.simulationcraft.org/nightly/?C=M;O=D').read().decode('utf-8')
    except URLError:
        logging.info("Could not access download directory on simulationcraft.org")
    # filename = re.search(r'<a href="(simc.+win64.+7z)">', html).group(1)
    filename = list(filter(None, re.findall(r'.+nonetwork.+|<a href="(simc.+win64.+7z)">', html)))[0]
    print(_("Latest simc: {filename}").format(filename=filename))

    # Download latest build of simc
    filepath = os.path.join(download_dir, filename)
    if not os.path.exists(filepath):
        url = 'http://downloads.simulationcraft.org/nightly/' + filename
        logging.info(_("Retrieving simc from url {} to {}.").format(url,
                                                                    filepath))
        urlretrieve(url, filepath)
    else:
        logging.debug(_("Latest simc version already downloaded at {}.").format(filename))

    # Unpack downloaded build and set simc_path
    settings.simc_path = os.path.join(download_dir, filename[:filename.find(".7z")][:-8], "simc.exe")
    splitter.simc_path = settings.simc_path
    if not os.path.exists(settings.simc_path):
        seven_zip_executables = ["7z.exe", "C:/Program Files/7-Zip/7z.exe"]
        for seven_zip_executable in seven_zip_executables:
            try:
                if not os.path.exists(seven_zip_executable):
                    logging.info(_("7Zip executable at '{}' does not exist.").format(seven_zip_executable))
                    continue
                cmd = seven_zip_executable + ' x "' + filepath + '" -aoa -o"' + download_dir + '"'
                logging.debug(_("Running unpack command '{}'").format(cmd))
                subprocess.call(cmd)

                # keep the latest 7z to remember current version, but clean up any other ones
                files = glob.glob(download_dir + '/simc*win64*7z')
                for f in files:
                    if not os.path.basename(f) == filename:
                        print(_("Removing old simc from '{}'.").format(os.path.basename(f)))
                        os.remove(f)
                break
            except Exception as e:
                print(_("Exception when unpacking: {}").format(e))
        else:
            raise RuntimeError(_("Could not unpack the auto downloaded SimulationCraft executable."
                                 "Please note that you need 7Zip installed at one of the following locations: {}.").
                               format(seven_zip_executables))
    else:
        print(_("Simc already exists at '{}'.").format(repr(settings.simc_path)))


def cleanup_subdir(subdir):
    if os.path.exists(subdir):
        if not settings.delete_temp_default and not settings.skip_questions:
            if input(_("Do you want to remove subfolder: {}? (Press y to confirm): ").format(subdir)) != _("y"):
                return
        logging.info(_("Removing subdir '{}'.").format(subdir))
        shutil.rmtree(subdir)


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


def cleanup(stages):
    logger.debug('Cleaning up')
    subdirs = [get_subdir(stage) for stage in range(1, stages + 1)]
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
            logging.debug(_("Simc executable exists at '{}', proceeding...").format(settings.simc_path))
        if os.name == "nt":
            if not settings.simc_path.endswith("simc.exe"):
                raise RuntimeError(_("Simc executable must end with 'simc.exe', and '{}' does not."
                                     "Please check your settings.py simc_path options.").format(settings.simc_path))

        analyzer_path = os.path.join(os.getcwd(), settings.analyzer_path, settings.analyzer_filename)
        if os.path.exists(analyzer_path):
            logging.info(_("Analyzer-file found at '{}'.").format(analyzer_path))
        else:
            raise RuntimeError(_("Analyzer-file not found at '{}', make sure you have a complete AutoSimc-Package.").
                               format(analyzer_path))

    # validate tier-set
    min_tier_sets = 0
    max_tier_sets = 6
    tier_sets = {"Tier27": (t27min, t27max)
                 }

    total_min = 0
    for tier_name, (tier_set_min, tier_set_max) in tier_sets.items():
        if tier_set_min < min_tier_sets:
            raise ValueError(_("Invalid tier set minimum ({} < {}) for tier '{}'").
                             format(tier_set_min, min_tier_sets, tier_name))
        if tier_set_max > max_tier_sets:
            raise ValueError(_("Invalid tier set maximum ({} > {}) for tier '{}'").
                             format(tier_set_max, max_tier_sets, tier_name))
        if tier_set_min > tier_set_max:
            raise ValueError(_("Tier set min > max ({} > {}) for tier '{}'")
                             .format(tier_set_min, tier_set_max, tier_name))
        total_min += tier_set_min

    if total_min > max_tier_sets:
        raise ValueError(_("All tier sets together have too much combined min sets ({}=sum({}) > {}).").
                         format(total_min, [t[0] for t in tier_sets.values()], max_tier_sets))

    # use a "safe mode", overwriting the values
    if settings.simc_safe_mode:
        logger.info('Using Safe Mode as specified in settings.')
        settings.simc_threads = 1

    if settings.default_error_rate_multiplier <= 0:
        raise ValueError(f'Invalid default_error_rate_multiplier ({settings.default_error_rate_multiplier}) <= 0')

    valid_grabbing_methods = 'target_error', 'top_n'
    if settings.default_grabbing_method not in valid_grabbing_methods:
        raise ValueError(f'Invalid settings.default_grabbing_method "{settings.default_grabbing_method}"". Valid options: {valid_grabbing_methods}')

        # Combine existing gems of the item with the gems supplied by --gems
        combined_gem_list = gems_on_gear
        combined_gem_list += gem_list
        combined_gem_list = stable_unique(combined_gem_list)
        # logging.debug("Combined gem list: {}".format(combined_gem_list))
        new_gems = get_gem_combinations(combined_gem_list, len(gems_on_gear))
        # logging.debug("New Gems: {}".format(new_gems))
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
        #         logging.debug("Gem permutations:")
        #         for i, comb in enumerate(new_combinations):
        #             logging.debug("Combination {}".format(i))
        #             for slot, item in comb.items():
        #                 logging.debug("{}: {}".format(slot, item))
        #             logging.debug("")
        return new_combinations

    def update_talents(self, talents):
        self.talents = talents

    def count_weekly_rewards(self):
        self.weeklyRewardCount = 0
        for item in self.items.values():
            if item.isWeeklyReward:
                self.weeklyRewardCount += 1

    def count_tier(self):
        self.t27 = 0
        for item in self.items.values():
            if item.tier_27:
                self.t27 += 1

    def count_legendaries(self):
        self.legendaries_equipped = 0
        for item in self.items.values():
            if item.item_id in shadowlands_legendary_ids:
                self.legendaries_equipped += 1

    def check_usable_before_talents(self):
        self.count_tier()
        self.count_weekly_rewards()
        self.count_legendaries()

        if self.legendaries_equipped > 1:
            return "too many legendaries equipped"
        if self.weeklyRewardCount > 1:
            return "too many weekly reward items equipped"
        if self.t27 < t27min:
            return "too few tier 27 items"
        if self.t27 > t27max:
            return "too many tier 27 items"

        return None

    def get_profile_name(self, valid_profile_number):
        # namingdata contains info for the profile-name
        namingData = {"T27": ""}

        for tier in ([27]):
            count = getattr(self, "t" + str(tier))
            tiername = "T" + str(tier)
            if count:
                pieces = 0
                if count >= 2:
                    pieces = 2
                if count >= 4:
                    pieces = 4
                    namingData[tiername] = "_{}_{}p".format(tiername, pieces)

        return str(valid_profile_number).rjust(self.max_profile_chars, "0")

    def get_profile(self):
        items = []
        # Hack for now to get Txx and L strings removed from items
        for item in self.items.values():
            items.append(item.output_str)
        return "\n".join(items)

    def write_to_file(self, filehandler, valid_profile_number, additional_options):
        profile_name = self.get_profile_name(valid_profile_number)

        filehandler.write("{}={}\n".format(self.profile.wow_class,
                                           str.replace(self.profile.profile_name, "\"", "") + "_" + profile_name))
        filehandler.write(self.profile.general_options)
        filehandler.write("\ntalents={}\n".format(self.talents))
        filehandler.write(self.get_profile())
        filehandler.write("\n{}\n".format(additional_options))
        filehandler.write("\n")


class Item:
    """WoW Item"""
    tiers = [27]

    def __init__(self, slot, is_weekly_reward, input_string=""):
        self._slot = slot
        self.name = ""
        self.item_id = 0
        self.bonus_ids = []
        self.enchant_ids = []
        self._gem_ids = []
        self.drop_level = 0
        self.tier_set = {}
        self.extra_options = {}
        self._isWeeklyReward = is_weekly_reward

        for tier in self.tiers:
            n = "T{}".format(tier)
            if self.name.startswith(n):
                setattr(self, "tier_{}".format(tier), True)
                self.name = self.name[len(n):]
            else:
                setattr(self, "tier_{}".format(tier), False)
        if len(input_string):
            self.parse_input(input_string.strip("\""))

        self._build_output_str()  # Pre-Build output string as good as possible

    @property
    def slot(self):
        return self._slot

    @slot.setter
    def slot(self, value):
        self._slot = value
        self._build_output_str()

    @property
    def isWeeklyReward(self):
        return self._isWeeklyReward

    @isWeeklyReward.setter
    def isWeeklyReward(self, value):
        self._isWeeklyReward = value
        self._build_output_str()

    @property
    def gem_ids(self):
        return self._gem_ids

    @gem_ids.setter
    def gem_ids(self, value):
        self._gem_ids = value
        self._build_output_str()

    def parse_input(self, input_string):
        parts = input_string.split(",")
        self.name = parts[0]

        for tier in self.tiers:
            n = "T{}".format(tier)
            if self.name.startswith(n):
                setattr(self, "tier_{}".format(tier), True)
                self.name = self.name[len(n):]
            else:
                setattr(self, "tier_{}".format(tier), False)

        splitted_name = self.name.split("--")
        if len(splitted_name) > 1:
            self.name = splitted_name[1]

        for s in parts[1:]:
            name, value = s.split("=")
            name = name.lower()
            if name == "id":
                self.item_id = int(value)
            elif name == "bonus_id":
                self.bonus_ids = [int(v) for v in value.split("/")]
            elif name == "enchant_id":
                self.enchant_ids = [int(v) for v in value.split("/")]
            elif name == "gem_id":
                self.gem_ids = [int(v) for v in value.split("/")]
            elif name == "drop_level":
                self.drop_level = int(value)
            else:
                if name not in self.extra_options:
                    self.extra_options[name] = []
                self.extra_options[name].append(value)

    def _build_output_str(self):
        self.output_str = "{}={},id={}". \
            format(self.slot,
                   self.name,
                   self.item_id)
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
                self.output_str += ",{}={}".format(name, value)

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
        iterables = iterables
        it = iterables[0]
        for item in iter(it):
            for items in product(*iterables[1:]):
                yield (item,) + items


# generate map of id->type pairs
def initWeaponData():
    # weapondata is directly derived from blizzard-datatables
    # Thanks to Theunderminejournal.com for providing the database:
    # http://newswire.theunderminejournal.com/phpMyAdmin
    # SELECT id, type
    # FROM `tblDBCItem`
    # WHERE type = 13 or type = 14 or type = 15 or type = 17
    # or type = 21 or type = 22 or type = 23 or type = 26
    #
    # type is important:
    # 13: onehand                                                   -mh, oh
    # 14: shield                                                    -oh
    # 15: bow                                                       -mh
    # 17: twohand (two twohanders are allowed for fury-warriors)    -mh, oh
    # 21: offhand-weapon                                            -oh
    # 22: offhand special stuff                                     -oh
    # 23: offhand                                                   -oh
    # 26: gun                                                       -mh
    #
    # WE REALLY DONT CARE if you can equip it or not, if it has str or int
    # we only use it to distinguish whether to put it into main_hand or off_hand slot
    #
    # therefore, if a warrior tries to sim a polearm, it would be assigned to the main_Hand (possibly two, if fury), but
    # the stats etc. would not be taken into account by simulationcraft
    # similar to weird combinations like bow and offhand or onehand and shield for druids
    # => disable those items or sell them, or implement a validation-check, no hunter needs a shield...

    global weapondata
    weapondata = {}
    with open('weapondata.json', "r", encoding='utf-8') as data_file:
        weapondata_json = json.load(data_file)

        for weapon in weapondata_json:
            weapondata[weapon['id']] = WeaponType(int(weapon['type']))
    # always create one offhand-item which is used as dummy for twohand-permutations
    weapondata["-1"] = WeaponType.DUMMY


def isValidWeaponPermutation(permutation, player_profile):
    mh_type = weapondata[str(permutation[10].item_id)]
    oh_type = weapondata[str(permutation[11].item_id)]

    # only gun or bow is equippable
    if (mh_type is WeaponType.BOW or mh_type is WeaponType.GUN) and oh_type is None:
        return True
    if player_profile.wow_class != "hunter" and (mh_type is WeaponType.BOW or mh_type is WeaponType.GUN):
        return False
    # only warriors can wield twohanders in offhand
    if player_profile.wow_class != "warrior" and oh_type is WeaponType.TWOHAND:
        return False
    # no true offhand in mainhand possible
    if mh_type is WeaponType.SHIELD or mh_type is WeaponType.OFFHAND:
        return False
    if player_profile.wow_class != "warrior" and mh_type is WeaponType.TWOHAND and (
            oh_type is WeaponType.OFFHAND or oh_type is WeaponType.SHIELD):
        return False

    return True


def permutate(args, player_profile):
    print(_("Combinations in progress..."))

    parsed_gear = collections.OrderedDict({})

    gear = player_profile.simc_options.get('gear')
    gearInBags = player_profile.simc_options.get('gearInBag')
    weeklyRewards = player_profile.simc_options.get('weeklyRewards')

    # concatenate gear in bags to normal gear-list
    for b in gearInBags:
        if b in gear:
            if len(gear[b]) > 0:
                currentGear = gear[b][0]
                if b == "finger" or b == "trinket":
                    currentGear = currentGear + "|" + gear[b][1]
                for foundGear in gearInBags.get(b):
                    currentGear = currentGear + "|" + foundGear
                gear[b] = currentGear
            else:
                gear[b] = gearInBags.get(b)

    # concatenate weekly rewards to normal gear-list
    for b in weeklyRewards:
        if b in gear:
            if len(gear[b]) > 0:
                currentGear = gear[b]
                if b == "finger" or b == "trinket":
                    currentGear = currentGear + "|" + gear[b][1]
                for foundGear in weeklyRewards.get(b):
                    currentGear = currentGear + "|" + foundGear
                gear[b] = currentGear
            else:
                gear[b] = weeklyRewards.get(b)

    for gear_slot in gear_slots:
        slot_base_name = gear_slot[0]  # First mentioned "correct" item name
        parsed_gear[slot_base_name] = []
        # create a dummy-item so no_offhand-combinations are not being dismissed later in the product-function
        if slot_base_name == "off_hand":
            item = Item("off_hand", False, "")
            item.item_id = -1
            parsed_gear["off_hand"] = [item]
        for entry in gear_slot:
            if entry in gear:
                if len(gear[entry]) > 0:
                    for s in gear[entry].split("|"):
                        in_weekly_rewards = False
                        if s in weeklyRewards[slot_base_name]:
                            in_weekly_rewards = True
                        parsed_gear[slot_base_name].append(Item(slot_base_name, in_weekly_rewards, s))
        if len(parsed_gear[slot_base_name]) == 0:
            # We havent found any items for that slot, add empty dummy item
            parsed_gear[slot_base_name] = [Item(slot_base_name, False, "")]


    logging.debug(_("Parsed gear: {}").format(parsed_gear))

    if args.gems is not None:
        splitted_gems = build_gem_list(args.gems)

    # Filter each slot to only have unique items, before doing any gem permutation.
    for key, value in parsed_gear.items():
        parsed_gear[key] = stable_unique(value)

    # This represents a dict of all options which will be permutated fully with itertools.product
    normal_permutation_options = collections.OrderedDict({})

    # Add talents to permutations
    l_talents = player_profile.simc_options.get("talents")
    talent_permutations = permutate_talents(l_talents)

    # Calculate max number of gem slots in equip. Will be used if we do gem permutations.
    if args.gems is not None:
        max_gem_slots = 0
        for _slot, items in parsed_gear.items():
            max_gem_on_item_slot = 0
            for item in items:
                if len(item.gem_ids) > max_gem_on_item_slot:
                    max_gem_on_item_slot = len(item.gem_ids)
            max_gem_slots += max_gem_on_item_slot

    # Add 'normal' gear to normal permutations, excluding trinket/rings
    gear_normal = {k: v for k, v in parsed_gear.items() if (not k == "finger" and not k == "trinket")}
    normal_permutation_options.update(gear_normal)

    # Calculate normal permutations
    normal_permutations = product(*normal_permutation_options.values())
    logging.debug(_("Building permutations matrix finished."))

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

        logging.debug(_("Input list for special permutation '{}': {}").format(name,
                                                                              entries))
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

        logging.debug(_("Got {num} permutations for {item_name}.").format(num=len(permutations),
                                                                          item_name=name))
        for p in permutations:
            logging.debug(p)

        # Remove equal id's
        if args.unique_jewelry:
            permutations = [p for p in permutations if p[0].item_id != p[1].item_id]
            logging.debug(_("Got {num} permutations for {item_name} after id filter.")
                          .format(num=len(permutations),
                                  item_name=name))
            for p in permutations:
                logging.debug(p)
        # Make unique
        permutations = stable_unique(permutations)
        logging.info(_("Got {num} permutations for {item_name} after unique filter.")
                     .format(num=len(permutations),
                             item_name=name))
        for p in permutations:
            logging.debug(p)

        entry_dict = {v: None for v in values}
        special_permutations[name] = [name, entry_dict, permutations]

    # Calculate & Display number of permutations
    max_nperm = 1
    for name, perm in normal_permutation_options.items():
        max_nperm *= len(perm)
    permutations_product = {_("normal gear&talents"): "{} ({})".format(max_nperm,
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
    logging.info(_("Max number of normal permutations: {}").format(max_nperm))
    logging.info(_("Number of permutations: {}").format(permutations_product))
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
            if isValidWeaponPermutation(perm_normal, player_profile):
                for perm_finger in special_permutations["finger"][2]:
                    for perm_trinket in special_permutations["trinket"][2]:
                        entries = perm_normal
                        entries += perm_finger
                        entries += perm_trinket
                        items = {e.slot: e for e in entries if type(e) is Item}
                        data = PermutationData(items, player_profile, max_profile_chars)
                        is_unusable_before_talents = data.check_usable_before_talents()
                        if not is_unusable_before_talents:
                            # add gem-permutations to gear
                            if args.gems is not None:
                                gem_permutations = data.permutate_gems(items, splitted_gems)
                            else:
                                gem_permutations = (items,)
                            for gem_permutation in gem_permutations:
                                data.items = gem_permutation
                                # Permutate talents after is usable check, since it is independent of the talents
                                for t in talent_permutations:
                                    data.update_talents(t)
                                    # Additional talent usable check could be inserted here.
                                    data.write_to_file(output_file, valid_profiles, additional_options)
                                    valid_profiles += 1
                                    processed += 1
                        else:
                            processed += len(talent_permutations) * gem_perms
                            if is_unusable_before_talents not in unusable_histogram:
                                unusable_histogram[is_unusable_before_talents] = 0
                            unusable_histogram[is_unusable_before_talents] += len(talent_permutations) * gem_perms
                        progress += 1
                        print_permutation_progress(valid_profiles, processed, max_nperm, start_time, max_profile_chars,
                                                   progress, max_progress)

    result = _("Finished permutations. Valid: {:n} of {:n} processed. ({:.2f}%)"). \
        format(valid_profiles,
               processed,
               100.0 * valid_profiles / max_nperm if max_nperm else 0.0)
    logging.info(result)

    # Not usable histogram debug output
    unusable_string = []
    for key, value in unusable_histogram.items():
        unusable_string.append("{:40s}: {:12b} ({:5.2f}%)".
                               format(key, value, value * 100.0 / max_nperm if max_nperm else 0.0))
    logging.info(_("Invalid profile statistics: [\n{}]").format("\n".join(unusable_string)))

    # Print checksum so we can check for equality when making changes in the code
    outfile_checksum = file_checksum(args.outputfile)
    logging.info(_("Output file checksum: {}").format(outfile_checksum))

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
            logger.warning(f'Result file "{file}"" is empty.')

    logger.debug(f'{len(files)} valid result files found in {subdir}.')
    logger.info(f'Checked all files in {subdir} : Everything seems to be alright.')


def get_subdir(stage):
    subdir = f'stage_{stage:n}'
    subdir = os.path.join(settings.temporary_folder_basepath, subdir)
    subdir = os.path.abspath(subdir)
    return subdir


def grab_profiles_for_stage(player_profile, stage, outputfile, stages):
    """Parse output/result files from previous stage and get number of profiles to simulate"""
    subdir_previous_stage = get_subdir(stage - 1)
    if stage == 1:
        num_generated_profiles = splitter.split(outputfile, get_subdir(stage), settings.splitting_size, player_profile.wow_class)
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
            filter_criterium = settings.default_top_n[stage - stages - 1]
        is_last_stage = (stage == stages)
        num_generated_profiles = splitter.grab_best(filter_by, filter_criterium, subdir_previous_stage, get_subdir(stage), outputfile, not is_last_stage)
    if num_generated_profiles:
        logger.info(f'Found {num_generated_profiles} profile(s) to simulate.')
    return num_generated_profiles


def check_profiles_from_stage(stage):
    subdir = get_subdir(stage)
    if not os.path.exists(subdir):
        return False
    files = os.listdir(subdir)
    files = [f for f in files if f.endswith(".simc")]
    files = [f for f in files if not f.endswith("arguments.simc")]
    files = [f for f in files if os.stat(os.path.join(subdir, f)).st_size > 0]
    return len(files)


def run_static_stage(player_profile, stage, scale, stages):
    if stage > stages:
        return
    logger.info('----------------------------------------------------')
    logger.info(f'***Entering static mode, STAGE {stage}***')
    is_last_stage = (stage == stages)
    try:
        num_iterations = settings.default_iterations[stage]
    except Exception:
        num_iterations = None
    if not num_iterations:
        raise ValueError(("Cannot run static mode and skip questions without default iterations set for stage {}.").format(stage))
    splitter.simulate(get_subdir(stage), "iterations", num_iterations, player_profile, stage, is_last_stage, scale)
    run_static_stage(player_profile, stage + 1, scale, stages)


def run_dynamic_stage(player_profile, num_generated_profiles, outputfile, scale, stages, previous_target_error=None, stage=1):
    if stage > stages:
        return
    logger.info('----------------------------------------------------')
    logger.info(f"Entering dynamic mode, STAGE {stage}")

    num_generated_profiles = grab_profiles_for_stage(player_profile, stage, outputfile, stages)

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
    is_last_stage = (stage == stages)
    splitter.simulate(get_subdir(stage), "target_error", target_error, player_profile, stage, is_last_stage, scale)
    run_dynamic_stage(player_profile, num_generated_profiles, outputfile, scale, stages, target_error, stage + 1)


def start_stage(player_profile, num_generated_profiles, stage, outputfile, scale, stages):
    logger.info('----------------------------------------------------')
    logger.info(f'Starting at stage {stage}')
    logger.info(f'You selected grabbing method "{settings.default_grabbing_method}".')
    mode_choice = int(settings.auto_choose_static_or_dynamic)
    valid_modes = (1, 2)
    if mode_choice not in valid_modes:
        raise RuntimeError(f'Invalid simulation mode "{mode_choice}" selected. Valid modes: {valid_modes}.')
    if mode_choice == 1:
        run_static_stage(player_profile, stage, scale, stages)
    elif mode_choice == 2:
        run_dynamic_stage(player_profile, num_generated_profiles, outputfile, scale, stages, None, stage)
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
                        "Python-Version {}.{}.x").format(sys.version, required_major, required_minor))


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

    start = datetime.datetime.now()

    logger.info(f'AutoSimC - Supported WoW-Version: {__version__}')

    args = parse_command_line_args()

    if args.sim:
        if not settings.auto_download_simc:
            if settings.check_simc_version:
                filename, latest = determineLatestSimcVersion()
                ondisc = determineSimcVersionOnDisc()
                if latest != ondisc:
                    logging.info(_("A newer SimCraft-version might be available for download! Version: {}").
                                 format(filename))
        autoDownloadSimc()
    validateSettings(args)

    initWeaponData()
    player_profile = AddonImporter.build_profile_simc_addon(args, gear_slots, Profile(), specdata)

    # can always be rerun since it is now deterministic
    permutator = Permutator(args.additionalfile, logger, player_profile, args.gems, args.unique_jewelry, args.outputfile)
    start = datetime.datetime.now()
    num_generated_profiles = permutator.generate_permutations()
    logger.debug(f'Permutating took {datetime.datetime.now() - start}.')

    if num_generated_profiles == 0:
        raise RuntimeError(('No valid profile combinations found.'
                            ' Please check the "Invalid profile statistics" output and adjust your'
                            ' input.txt and settings.py.'))
    if args.sim:
        if num_generated_profiles and num_generated_profiles > 1000:
            logger.warning('Beware: Computation with Simcraft might take a VERY long time with this amount of profiles!')

    if args.sim:
        player_profile = add_fight_style(player_profile)
        if args.sim == 'stage1' or args.sim == 'all':
            start_stage(player_profile, num_generated_profiles, 1, args.outputfile, args.scale, args.stages)
        if args.sim == 'stage2':
            start_stage(player_profile, None, 2, args.outputfile, args.scale, args.stages)
        if args.sim == 'stage3':
            start_stage(player_profile, None, 3, args.outputfile, args.scale, args.stages)

    end = datetime.datetime.now()
    if settings.clean_up:
        cleanup(args.stages)
    logger.info(f'Total simulation took {end - start}.')
    logger.info('AutoSimC finished correctly.')


if __name__ == "__main__":
    try:
        main()
        logging.shutdown()
    except Exception as ex:
        logger.error(f'Error: {ex}', exc_info=True)
        sys.exit(1)
