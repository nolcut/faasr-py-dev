import sys
import logging

from collections import namedtuple


logger = logging.getLogger(__name__)


def faasr_get_s3_creds(config, server_name=""):
    """
    Returns credentials needed to create an Apache Pyarrow S3FileSystem instance

    Arguments:
        config: FaaSr payload dict
        server_name: str -- name of S3 data store to get credentials from
    Returns:
        dict: A dict with the fields
        (bucket, region, endpoint, secret_key, access_key, anonymous)
    """
    # fetch server name if one is not provided
    if server_name == "":
        server_name = config["DefaultDataStore"]

    # ensure that server name provided is valid
    if server_name not in config["DataStores"]:
        err_msg = f'{{"faasr_get_arrow":"Invalid data server name: {server_name}"}}\n'
        print(err_msg)
        sys.exit(1)

    target_s3 = config["DataStores"][server_name]

    if not target_s3.get("Anonymous") or len(target_s3["Anonymous"]) == 0:
        anonymous = False
    else:
        match (target_s3["Anonymous"].tolower()):
            case "true":
                anonymous = True
            case "false":
                anonymous = False
            case _:
                anonymous = False

    # if the connection is anonymous, don't return keys
    if anonymous:
        secret_key = None
        access_key = None
    else:
        try:
            secret_key = target_s3["SecretKey"]
            access_key = target_s3["AccessKey"]
        except KeyError as e:
            err_msg = f'{{"faasr_get_arrow":"Missing key in S3 data store: {e}"}}\n'
            print(err_msg)
            sys.exit(1)

    # return credentials as namedtuple
    return {
        "bucket": target_s3["Bucket"],
        "region": target_s3["Region"],
        "endpoint": target_s3["Endpoint"],
        "secret_key": secret_key,
        "access_key": access_key,
        "anonymous": anonymous,
    }
