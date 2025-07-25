import uvicorn
import sys
import requests
import logging

from pydantic import BaseModel
from fastapi import FastAPI
from FaaSr_py.helpers.rank import faasr_rank
from FaaSr_py.s3_api import (
    faasr_log,
    faasr_put_file,
    faasr_get_file,
    faasr_delete_file,
    faasr_get_folder_list,
    faasr_get_s3_creds,
)


faasr_api = FastAPI()
logger = logging.getLogger(__name__)
valid_functions = {
    "faasr_get_file",
    "faasr_put_file",
    "faasr_delete_file",
    "faasr_get_folder_list",
    "faasr_log",
    "faasr_rank"
}


class Request(BaseModel):
    ProcedureID: str
    Arguments: dict | None = None


class Response(BaseModel):
    Success: bool
    Data: dict | None = None
    Message: str | None = None


class Return(BaseModel):
    FunctionResult: bool | None = None


class Result(BaseModel):
    FunctionResult: bool | None = None
    Error: bool | None = None
    Message: str | None = None


class Exit(BaseModel):
    Error: bool | None = None
    Message: str | None = None


def register_request_handler(faasr_instance):
    """"
    Setup FastAPI request handlers for FaaSr functions

    Arguments:
        faasr_instance: FaaSr payload dict
    """
    return_val = None
    message = None
    error = False

    @faasr_api.post("/faasr-action")
    def faasr_request_handler(request: Request):
        """
        Handler for FaaSr function requests
        """
        nonlocal error
        print(f'{{"Processing request": "{request.ProcedureID}"}}', flush=True)
        args = request.Arguments or {}
        return_obj = Response(Success=True, Data={})
        try:
            match request.ProcedureID:
                case "faasr_log":
                    faasr_log(config=faasr_instance, **args)
                case "faasr_put_file":
                    faasr_put_file(config=faasr_instance, **args)
                case "faasr_get_file":
                    faasr_get_file(config=faasr_instance, **args)
                case "faasr_delete_file":
                    faasr_delete_file(config=faasr_instance, **args)
                case "faasr_get_folder_list":
                    return_obj.Data["folder_list"] = faasr_get_folder_list(
                        config=faasr_instance, **args
                    )
                case "faasr_rank":
                    return_obj.Data["rank"] = faasr_rank(config=faasr_instance)
                case "faasr_get_s3_creds":
                    return_obj.Data["s3_creds"] = faasr_get_s3_creds(
                        config=faasr_instance, **args
                    )
                case _:
                    print(f"{{faasr_server.py: ERROR -- {request.ProcedureID} is not a valid FaaSr function call}}")
                    error = True
                    sys.exit(1)
        except Exception as e:
            err_msg = f"{{faasr_server: ERROR -- failed to invoke {request.ProcedureID} -- {e}}}"
            faasr_log(config=faasr_instance, log_message=err_msg)
            print(err_msg)
            error = True
            sys.exit(1)
        return return_obj

    @faasr_api.post("/faasr-return")
    def faasr_return_handler(return_obj: Return):
        """
        Handler for FaaSr function return values
        """
        nonlocal return_val
        return_val = return_obj.FunctionResult
        return Response(Success=True)

    @faasr_api.post("/faasr-exit")
    def faasr_get_exit_handler(exit_obj: Exit):
        """
        Handler for FaaSr function exit values
        """
        nonlocal error, message
        print(exit_obj)
        if exit_obj.Error:
            error = True
            message = exit_obj.Message
        return Response(Success=True)

    @faasr_api.get("/faasr-get-return")
    def faasr_get_return_handler():
        """
        Handler to get the return value from the FaaSr function
        """
        return Result(FunctionResult=return_val, Error=error, Message=message)


@faasr_api.get("/faasr-echo")
def faasr_echo(message: str):
    return {"message": message}


def wait_for_server_start(port):
    """
    Polls the server until it's ready to accept requests
    Arguments:
        port: int -- port the server is running on
    """
    while True:
        try:
            r = requests.get(
                f"http://127.0.0.1:{port}/faasr-echo", params={"message": "echo"}
            )
            message = r.json()["message"]
            if message == "echo":
                break
        except Exception:
            continue


# starts a server listening on localhost
def run_server(faasr_instance, port):
    """
    Starts a FastAPI server to handle FaaSr requests
    
    Arguments:
        faasr_instance: FaaSr payload dict
        port: int -- port to run the server on
    """
    register_request_handler(faasr_instance)
    config = uvicorn.Config(faasr_api, host="127.0.0.1", port=port)
    server = uvicorn.Server(config)
    server.run()
