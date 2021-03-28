class Profile:
    """Represent global profile data"""

    def __init__(self):
        self.args = None
        self.simc_options = ''
        self.wow_class = ''
        self.profile_name = ''
        self.class_spec = ''
        self.class_role = ''
        self.general_options = ''

    def __str__(self):
        return f'{{"args": "{self.args}", "simc_options": {self.simc_options}, "wow_class": "{self.wow_class}", "profile_name": "{self.profile_name}", "class_spec": "{self.class_spec}", "class_role": "{self.class_role}", "general_options": "{self.general_options}"}}'

    def __repr__(self):
        return f'{{"args": "{self.args}", "simc_options": {self.simc_options}, "wow_class": "{self.wow_class}", "profile_name": "{self.profile_name}", "class_spec": "{self.class_spec}", "class_role": "{self.class_role}", "general_options": "{self.general_options}"}}'
