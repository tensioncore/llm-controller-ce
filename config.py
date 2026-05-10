from bootstrap_config import get_bootstrap_config


def reload_bootstrap_values():
    runtime = get_bootstrap_config()
    return (
        runtime["db_host"],
        runtime["db_port"],
        runtime["db_user"],
        runtime["db_password"],
        runtime["db_name"],
    )


DB_HOST, DB_PORT, DB_USER, DB_PASS, DB_NAME = reload_bootstrap_values()
