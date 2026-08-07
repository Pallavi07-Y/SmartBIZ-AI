class ConversationMemory:

    def __init__(self):

        self.history = []
        self.last_entity = None
        self.state = {}


    def add_message(
        self,
        role,
        content
    ):

        self.history.append(

            {
                "role": role,
                "content": content
            }

        )


    def get_history(self):

        return self.history


    def set_last_entity(self, entity):

        self.last_entity = entity


    def get_last_entity(self):

        return self.last_entity


    def set_state(self, key, value):

        self.state[key] = value


    def get_state(self, key, default=None):

        return self.state.get(key, default)


    def clear_state(self):

        self.state = {}


    def clear(self):

        self.history = []
        self.last_entity = None
        self.state = {}