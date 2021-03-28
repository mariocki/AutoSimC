
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

        for split_text in parts[1:]:
            name, value = split_text.split("=")
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
