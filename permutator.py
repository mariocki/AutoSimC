
import itertools
import collections
import copy
import datetime
import hashlib
from item import Item
from staticdata import gear_slots, gem_ids


class Permutator:
    """Data for each permutation"""

    def __init__(self, additional_filename, logger, player_profile, gems, unique_jewelry, outputfile):
        self.additional_filename = additional_filename
        self.player_profile = player_profile
        self.max_profile_chars = 0
        self.logger = logger
        self.gems = gems
        self.unique_jewelry = unique_jewelry
        self.outputfile = outputfile

    def _get_gem_combinations(self, gems_to_use, num_gem_slots):
        if num_gem_slots <= 0:
            return []
        combinations = itertools.combinations_with_replacement(gems_to_use, r=num_gem_slots)
        return list(combinations)

    def _permutate_gems(self, items, gem_list):
        gems_on_gear = []
        gear_with_gems = {}
        for slot, gear in items.items():
            gems_on_gear += gear.gem_ids
            gear_with_gems[slot] = len(gear.gem_ids)

        self.logger.debug(f'gems on gear: {gems_on_gear}')
        if len(gems_on_gear) == 0:
            return

        # Combine existing gems of the item with the gems supplied by --gems
        combined_gem_list = gems_on_gear
        combined_gem_list += gem_list
        combined_gem_list = self._stable_unique(combined_gem_list)
        self.logger.debug(f'Combined gem list: {combined_gem_list}')
        new_gems = self._get_gem_combinations(combined_gem_list, len(gems_on_gear))
        self.logger.debug(f'New Gems: {new_gems}')
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
            self.logger.debug('Gem permutations:')
            for i, combination in enumerate(new_combinations):
                self.logger.debug(f'Combination {i}')
                for slot, item in combination.items():
                    self.logger.debug(f'{slot}: {item}')
                self.logger.debug('')
        return new_combinations

    def _format_profile_for_simc(self, items_to_format):
        items = []
        # Hack for now to get Txx and L strings removed from items
        for item in items_to_format.values():
            items.append(item.output_str)
        return "\n".join(items)

    def _chop_microseconds(self, delta):
        """Chop microseconds from a timedelta object"""
        return delta - datetime.timedelta(microseconds=delta.microseconds)

    def _write_to_file(self, filehandler, valid_profile_number, additional_options, talents, items):
        profile_name = str(valid_profile_number).rjust(self.max_profile_chars, "0")

        filehandler.write("{}={}\n".format(self.player_profile.wow_class, str.replace(self.player_profile.profile_name, "\"", "")+"_"+profile_name))
        filehandler.write(self.player_profile.general_options)
        filehandler.write("\ntalents={}\n".format(talents))
        filehandler.write(self._format_profile_for_simc(items))
        filehandler.write("\n{}\n".format(additional_options))
        filehandler.write("\n")

    def _permutate_talents(self, talents_list):
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
            self.logger.debug(f'Talent combination input: {current_talents}')

        # Use some itertools magic to unpack the product of all talent combinations
        talent_product = [itertools.product(*t) for t in all_talent_combinations]
        talent_product = list(itertools.chain(*talent_product))

        # Format each permutation back to a nice talent string.
        permuted_talent_strings = ["".join(s) for s in talent_product]
        permuted_talent_strings = self._stable_unique(permuted_talent_strings)
        self.logger.debug(f'Talent combinations: {permuted_talent_strings}')
        return permuted_talent_strings

    def _print_permutation_progress(self, valid_profiles, current, maximum, start_time, max_profile_chars, progress, max_progress):
        # output status every 5000 permutations, user should get at least a minor progress shown; also does not slow down
        # computation very much
        print_every_n = max(int(50000 / (maximum / max_progress)), 1)
        if progress % print_every_n == 0 or progress == max_progress:
            pct = 100.0 * current / maximum
            elapsed = datetime.datetime.now() - start_time
            bandwith = current / 1000 / elapsed.total_seconds() if elapsed.total_seconds() else 0.0
            bandwith_valid = valid_profiles / 1000 / elapsed.total_seconds() if elapsed.total_seconds() else 0.0
            elapsed = self._chop_microseconds(elapsed)
            remaining_time = elapsed * (100.0 / pct - 1.0) if current else 'NaN'
            if current > maximum:
                remaining_time = datetime.timedelta(seconds=0)
            if isinstance(remaining_time, datetime.timedelta):
                remaining_time = self._chop_microseconds(remaining_time)
            valid_pct = 100.0 * valid_profiles / current if current else 0.0
            self.logger.info("Processed {}/{} ({:5.2f}%) valid {} ({:5.2f}%) elapsed_time {} "
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

    def _stable_unique(self, seq):
        """
        Filter sequence to only contain unique elements, in a stable order
        This is a replacement for x = list(set(x)), which does not lead to
        deterministic or 'stable' output.
        Credit to https://stackoverflow.com/a/480227
        """
        seen = set()
        seen_add = seen.add
        return [x for x in seq if not (x in seen or seen_add(x))]

    def _build_gem_list(self, gem_lists):
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
            gems = self._stable_unique(gems)
            sorted_gem_list += gems
        self.logger.debug(f'Parsed gem list to permutate: {sorted_gem_list}')
        return sorted_gem_list

    def _file_checksum(self, filename):
        sha256_hasher = hashlib.sha256()
        with open(filename, "rb") as file_pointer:
            for chunk in iter(lambda: file_pointer.read(4096), b""):
                sha256_hasher.update(chunk)
        return sha256_hasher.hexdigest()

    def _get_additional_input(self):
        input_encoding = 'utf-8'
        options = []
        try:
            with open(self.additional_filename, "r", encoding=input_encoding) as file_pointer:
                for line in file_pointer:
                    if not line.startswith("#"):
                        options.append(line)

        except UnicodeDecodeError as ex:
            raise RuntimeError("""AutoSimC could not decode your additional input file '{file}' with encoding '{enc}'.
            Please make sure that your text editor encodes the file as '{enc}',
            or as a quick fix remove any special characters from your character name.""".format(file=self.additional_filename, enc=input_encoding)) from ex

        return "".join(options)

    def _product(self, *iterables):
        """
        Custom product function as a generator, instead of itertools.product
        This uses way less memory than itertools.product, because it is a generator only yielding a single item at a time.
        requirement for this is that each iterable can be restarted.
        Thanks to https://stackoverflow.com/a/12094519
        """
        if len(iterables) == 0:
            yield ()
        else:
            iterator = iterables[0]
            for item in iter(iterator):
                for items in self._product(*iterables[1:]):
                    yield (item,) + items

    def permutate(self):
        self.logger.info('Calculating Permutations...')

        parsed_gear = collections.OrderedDict({})

        gear = self.player_profile.simc_options.get('gear')
        gear_in_bags = self.player_profile.simc_options.get('gearInBag')

        # concatenate gear in bags to normal gear-list
        for gear_in_bag in gear_in_bags:
            if gear_in_bag in gear:
                if len(gear[gear_in_bag]) > 0:
                    current_gear = gear[gear_in_bag][0]
                    if gear_in_bag == "finger" or gear_in_bag == "trinket":
                        current_gear = current_gear + "|" + gear[gear_in_bag][1]
                    for found_gear in gear_in_bags.get(gear_in_bag):
                        current_gear = current_gear + '|' + found_gear
                    gear[gear_in_bag] = current_gear

        for gear_slot in gear_slots:
            slot_base_name = gear_slot[0]  # First mentioned "correct" item name
            parsed_gear[slot_base_name] = []
            for entry in gear_slot:
                if entry in gear:
                    if len(gear[entry]) > 0:
                        for split_section in gear[entry].split('|'):
                            parsed_gear[slot_base_name].append(Item(slot_base_name, split_section))
            if len(parsed_gear[slot_base_name]) == 0:
                # We havent found any items for that slot, add empty dummy item
                parsed_gear[slot_base_name] = [Item(slot_base_name, "")]

        self.logger.debug(f'Parsed gear: {parsed_gear}')

        if self.gems is not None:
            splitted_gems = self._build_gem_list(self.gems)

        # Filter each slot to only have unique items, before doing any gem permutation.
        for key, value in parsed_gear.items():
            parsed_gear[key] = self._stable_unique(value)

        # This represents a dict of all options which will be permutated fully with itertools.product
        normal_permutation_options = collections.OrderedDict({})

        # Add talents to permutations
        # l_talents = player_profile.config['Profile'].get("talents", "")
        l_talents = self.player_profile.simc_options.get('talents')
        talent_permutations = self._permutate_talents(l_talents)

        # Calculate max number of gem slots in equip. Will be used if we do gem permutations.
        max_gem_slots = 0
        if self.gems is not None:
            for _slot, items in parsed_gear.items():
                max_gem_on_item_slot = 0
                for item in items:
                    if len(item.gem_ids) > max_gem_on_item_slot:
                        max_gem_on_item_slot = len(item.gem_ids)
                max_gem_slots += max_gem_on_item_slot

        # no gems on gear so no point calculating gem permutations
        if max_gem_slots == 0:
            self.gems = None

        # Add 'normal' gear to normal permutations, excluding trinket/rings
        gear_normal = {k: v for k, v in parsed_gear.items() if (not k == 'finger' and not k == 'trinket')}
        normal_permutation_options.update(gear_normal)

        # Calculate normal permutations
        normal_permutations = self._product(*normal_permutation_options.values())
        self.logger.debug('Building permutations matrix finished.')

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

            self.logger.debug(f'Input list for special permutation "{name}": {entries}')
            if self.unique_jewelry:
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

            self.logger.debug(f'Got {len(permutations)} permutations for {name}.')
            for permutation in permutations:
                self.logger.debug(permutation)

            # Remove equal id's
            if self.unique_jewelry:
                permutations = [permutation for permutation in permutations if permutation[0].item_id != permutation[1].item_id]
            self.logger.debug(f'Got {len(permutations)} permutations for {name} after id filter.')
            for permutation in permutations:
                self.logger.debug(permutation)

            # Make unique
            permutations = self._stable_unique(permutations)
            self.logger.debug(f'Got {len(permutations)} permutations for {name} after unique filter.')
            for permutation in permutations:
                self.logger.debug(permutation)

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
        if self.gems is not None:
            max_num_gems = max_gem_slots + len(splitted_gems)
            gem_perms = len(list(itertools.combinations_with_replacement(range(max_gem_slots), max_num_gems)))
            max_nperm *= gem_perms
            permutations_product["gems"] = gem_perms
        permutations_product["talents"] = len(talent_permutations)
        self.logger.info(f'Max number of normal permutations: {max_nperm}')
        self.logger.info(f'Number of permutations: {permutations_product}')
        max_profile_chars = len(str(max_nperm))  # String length of max_nperm

        # Get Additional options string
        additional_options = self._get_additional_input()

        # Start the permutation!
        processed = 0
        progress = 0  # Separate progress variable not counting gem and talent combinations
        max_progress = max_nperm / gem_perms / len(talent_permutations)
        valid_profiles = 0
        start_time = datetime.datetime.now()
        unusable_histogram = {}  # Record not usable reasons
        with open(self.outputfile, 'w') as output_file:
            for perm_normal in normal_permutations:
                for perm_finger in special_permutations["finger"][2]:
                    for perm_trinket in special_permutations["trinket"][2]:
                        entries = perm_normal
                        entries += perm_finger
                        entries += perm_trinket
                        items = {e.slot: e for e in entries if isinstance(e, Item)}
                        # add gem-permutations to gear
                        if self.gems is not None:
                            gem_permutations = self._permutate_gems(items, splitted_gems)
                        else:
                            gem_permutations = (items,)
                        if gem_permutations is not None:
                            for gem_permutation in gem_permutations:
                                # Permutate talents after is usable check, since it is independent of the talents
                                for talent_permutation in talent_permutations:
                                    # Additional talent usable check could be inserted here.
                                    self._write_to_file(output_file, valid_profiles, additional_options, talent_permutation, gem_permutation)
                                    valid_profiles += 1
                                    processed += 1
                        progress += 1
                        self._print_permutation_progress(valid_profiles, processed, max_nperm, start_time, max_profile_chars, progress, max_progress)

        result = (f'Finished permutations. Valid: {valid_profiles:n} of {processed:n} processed. ({100.0 * valid_profiles / max_nperm if max_nperm else 0.0:.2f}%)')
        self.logger.info(result)

        # Not usable histogram debug output
        unusable_string = []
        for key, value in unusable_histogram.items():
            unusable_string.append(f'{key:40s}: {value:12b} ({value * 100.0 / max_nperm if max_nperm else 0.0:5.2f}%)')
        if len(unusable_string) > 0:
            self.logger.info(('Invalid profile statistics: [\n{}]').format("\n".join(unusable_string)))

        # Print checksum so we can check for equality when making changes in the code
        outfile_checksum = self._file_checksum(self.outputfile)
        self.logger.info(f'Output file checksum: {outfile_checksum}')

        return valid_profiles
