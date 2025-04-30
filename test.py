Kek = "1"
def admin_only(f):

    def wrapper_func():
        if Kek != 1:
            return "You are not authorized"
        return f()

    return wrapper_func()

@admin_only
def kek():
    test = 1+2
    print(test)