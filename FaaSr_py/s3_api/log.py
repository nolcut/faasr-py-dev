import logging
import os
import sys
from pathlib import Path

from FaaSr_py.config.debug_config import global_config
from FaaSr_py.helpers.s3_helper_functions import (
    get_default_log_boto3_client,
    get_invocation_folder,
    get_logging_server,
)

logger = logging.getLogger(__name__)


def faasr_log(faasr_payload, log_message):
    """
    Logs a message

    Arguments:
        faasr_payload: FaaSr payload dict
        log_message: str -- message to log
    """
    if not log_message:
        logger.error("ERROR -- log_message is empty")
        sys.exit(1)

    log_folder = get_invocation_folder(faasr_payload)
    log_path = log_folder / faasr_payload.log_file

    if global_config.USE_LOCAL_FILE_SYSTEM:
        # make log dir
        local_log_path = Path(global_config.LOCAL_FILE_SYSTEM_DIR / log_path)
        local_log_path.parent.mkdir(parents=True, exist_ok=True)

        # write log
        logs = f"{log_message}\n"
        with open(local_log_path, "a") as f:
            f.write(logs)
    else:
        # Get the logging data store from payload
        log_server_name = get_logging_server(faasr_payload)

        if log_server_name not in faasr_payload["DataStores"]:
            logger.error(f"Invalid logging server name: {log_server_name}")
            sys.exit(1)

        s3_client = get_default_log_boto3_client(faasr_payload)

        log_download_path = Path("/tmp") / log_path
        Path(log_download_path).parent.mkdir(parents=True, exist_ok=True)

        bucket = faasr_payload["DataStores"][log_server_name]["Bucket"]

        # Check if the log file already exists
        check_log_file = s3_client.list_objects_v2(Bucket=bucket, Prefix=str(log_path))

        # Download the log if it exists
        try:
            if "Contents" in check_log_file and len(check_log_file["Contents"]) != 0:
                if os.path.exists(log_download_path):
                    os.remove(log_download_path)
                s3_client.download_file(
                    Bucket=bucket, Key=str(log_path), Filename=str(log_download_path)
                )
        except s3_client.exceptions.ClientError as e:
            logger.error(f"Error downloading log file: {e}")
            sys.exit(1)

        # Write to log
        logs = f"{log_message}\n"
        with open(log_download_path, "a") as f:
            f.write(logs)

        # Upload log back to S3
        try:
            with open(log_download_path, "rb") as log_data:
                s3_client.put_object(Bucket=bucket, Body=log_data, Key=str(log_path))
        except s3_client.exceptions.ClientError as e:
            logger.error(f"Error reuploading log file: {e}")
            sys.exit(1)

        logger.debug("Log succesfully uploaded")
