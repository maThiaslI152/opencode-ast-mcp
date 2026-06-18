import sqlite3


class AuthManager:
    def __init__(self, db_path):
        self.db = sqlite3.connect(db_path)

    def authenticate(self, user, password):
        query = "SELECT * FROM users WHERE user = ? AND pass = ?"
        return self.db.execute(query, (user, password))

    def revoke(self, token):
        pass


def my_auth_logic(user, password):
    mgr = AuthManager("db")
    return mgr.authenticate(user, password)
