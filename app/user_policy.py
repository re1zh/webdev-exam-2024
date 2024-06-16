from flask_login import current_user

class UsersPolicy:
    def __init__(self, user):
        self.user = user

    def create_book(self):
        return current_user.is_admin()

    def delete_book(self):
        return current_user.is_admin()

    def edit_book(self):
        return current_user.is_admin() or current_user.is_moderator()

    def create_review(self):
        return True

    def edit_review(self):
        return current_user.is_admin() or current_user.is_moderator()