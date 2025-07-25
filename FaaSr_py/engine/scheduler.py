import requests
import re
import copy
import json
import sys
import logging
import boto3

from FaaSr_py.engine.faasr_payload import FaaSr
from FaaSr_py.config.debug_config import global_config
from FaaSr_py.s3_api import faasr_log


logger = logging.getLogger(__name__)


class Scheduler:
    """
    Handles scheduling of next functions in the DAG
    """
    def __init__(self, faasr: FaaSr):
        if not isinstance(faasr, FaaSr):
            err_msg = "{scheduler.py: initializer must be FaaSr instance}"
            print(err_msg)
            sys.exit(1)
        self.faasr = faasr

    def trigger(self, return_val=None):
        """
        Triggers the next actions in the DAG

        Arguments:
            return_val: any -- value returned by the user function, used for conditionals
        """
        # Get a list of the next functions to invoke
        curr_func = self.faasr["FunctionInvoke"]
        invoke_next = self.faasr["FunctionList"][curr_func]["InvokeNext"]
        if not isinstance(invoke_next, list):
            invoke_next = [invoke_next]

        # If there is no more triggers, then return
        if not invoke_next:
            msg = f'{{"faasr_trigger":"no triggers for {curr_func}"}}\n'
            print(msg)
            faasr_log(self.faasr, msg)
            return

        # Ensure that function returned a value if conditionals are present
        if contains_dict(invoke_next) and return_val is None:
            err_msg = '{"faasr_trigger":"ERROR -- InvokeNext contains conditionals but function did not return a value"}'
            print(err_msg)
            faasr_log(self.faasr, err_msg)
            sys.exit(1)

        for next_trigger in invoke_next:
            if isinstance(next_trigger, dict):
                conditional_invoke_next = next_trigger.get(str(return_val))
                if isinstance(conditional_invoke_next, str):
                    self.trigger_func(conditional_invoke_next)
                else:
                    for func in conditional_invoke_next:
                        self.trigger_func(func)
            else:
                self.trigger_func(next_trigger)

    def trigger_func(self, function):
        """
        Handles a single trigger

        Arguments:
            function: str -- name of the function to trigger
        """
        # Split function name and rank if needed
        parts = re.split(r"[()]", function)
        if len(parts) > 1:
            function = parts[0]
            rank_num = int(parts[1])
        else:
            rank_num = 1

        self.faasr["FunctionInvoke"] = function
        next_server = self.faasr["FunctionList"][function]["FaaSServer"]

        if global_config.SKIP_REAL_TRIGGERS:
            print("DEBUG MODE -- SKIPPING REAL TRIGGERS")

        for rank in range(1, rank_num + 1):
            if rank_num > 1:
                self.faasr["FunctionList"][function]["Rank"] = f"{rank}/{rank_num}"

            if next_server not in self.faasr["ComputeServers"]:
                err_msg = f'{{"faasr_trigger":"invalid server name: {next_server}"}}\n'
                print(err_msg)
                faasr_log(self.faasr, err_msg)
                break

            next_compute_server = self.faasr["ComputeServers"][next_server]
            next_server_type = next_compute_server["FaaSType"]

            if not global_config.SKIP_REAL_TRIGGERS:
                match (next_server_type):
                    case "OpenWhisk":
                        self.invoke_ow(next_compute_server, function)
                    case "Lambda":
                        self.invoke_lambda(next_compute_server, function)
                    case "GitHubActions":
                        self.invoke_gh(next_compute_server, function)
            else:
                msg = f"SIMULATED TRIGGER: {function}"
                if rank_num > 1:
                    msg += f".{rank}"
                print(msg)

    def invoke_gh(self, next_compute_server, function):
        """
        Trigger GH function

        Arguments:
            next_compute_server: dict -- next compute server configuration
            function: str -- name of the function to invoke
        """
        # Get env values for GH actions
        pat = next_compute_server["Token"]
        username = next_compute_server["UserName"]
        reponame = next_compute_server["ActionRepoName"]
        repo = f"{username}/{reponame}"
        if not function.endswith(".ml") and not function.endswith(".yaml"):
            workflow_file = f"{function}.yml"
        else:
            workflow_file = function
        git_ref = next_compute_server["Branch"]

        # Create copy of faasr payload
        faasr_git = copy.deepcopy(self.faasr.get_complete_workflow())

        # Create payload input
        overwritten_files = self.faasr.get_overwritten_fields()
        json_overwritten = json.dumps(overwritten_files)
        # to-do: if UseSecretStore of next function == True, then send secrets -- secrets = self.faasr.get_secrets()
        inputs = {
            "OVERWRITTEN": json_overwritten,
            "PAYLOAD_URL": self.faasr.url,
        }

        # Create url for GitHub API
        url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow_file}/dispatches"

        # Create body for POST request
        body = {"ref": git_ref, "inputs": inputs}

        # Create headers for POST request
        post_headers = {
            "Authorization": f"token {pat}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        # Issue POST request
        response = requests.post(url=url, json=body, headers=post_headers)

        # Log response
        if response.status_code == 204:
            succ_msg = f"{{faasr_trigger: GitHub Action: Successfully invoked: {self.faasr['FunctionInvoke']}}}\n"
            print(succ_msg)
            faasr_log(self.faasr, succ_msg)
        elif response.status_code == 401:
            err_msg = "{faasr_trigger: GitHub Action: Authentication failed, check the credentials}\n"
            print(err_msg)
            faasr_log(self.faasr, err_msg)
        elif response.status_code == 404:
            err_msg = f"{{faasr_trigger: GitHub Action: Cannot find the destination, check the repo name: {repo} and workflow name: {workflow_file}}}\n"
            print(err_msg)
            faasr_log(self.faasr, err_msg)
        elif response.status_code == 422:
            message = response.json().get("message")
            if message:
                err_msg = f"{{'faasr_trigger: GitHub Action': 'Cannot find the destination -- {message}'}}\n"
            else:
                err_msg = f"{{'faasr_trigger: GitHub Action': 'Cannot find the destination -- check ref {git_ref}'}}\n"
            print(err_msg)
            faasr_log(self.faasr, err_msg)
        else:
            if response:
                message = response.json().get("message")
                if message:
                    err_msg = f"{{faasr_trigger: GitHub Action: error when invoking function -- {message}}}\n"
                else:
                    err_msg = "{{faasr_trigger: GitHub Action: unknown error happens when invoke next function}}\n"
                print(err_msg)
                faasr_log(self.faasr, err_msg)
            else:
                err_msg = f"{{faasr_trigger: GitHub Action: unknown error when invoking {function}}}\n"
                print(err_msg)
                faasr_log(self.faasr, err_msg)

    # to-do
    def invoke_lambda(self, next_compute_server, function):
        """
        Trigger AWS Lambda function

        Arguments:
            next_compute_server: dict -- next compute server configuration
            function: str -- name of the function to invoke
        """
        # Create client for invoking lambda function
        lambda_client = boto3.client(
            "lambda",
            aws_access_key_id=next_compute_server["AccessKey"],
            aws_secret_access_key=next_compute_server["SecretKey"],
            region_name=next_compute_server["Region"],
        )

        # Invoke lambda function 
        # to-do: lambda function should take URL of payload & overwritten fields (not payload itself)
        # as input, and secrets if "UseSecretStore" is False for next_compute_server
        try:
            response = lambda_client.invoke(
                FunctionName=function,
                Payload=json.dumps(self.faasr.get_complete_workflow()),
            )
        except Exception as e:
            err_msg = f"{{\"faasr_trigger\": \"Error invoking function: {self.faasr['FunctionInvoke']} -- {e}\"}}\n"
            print(err_msg)
            faasr_log(self.faasr, err_msg)
            return

        if "StatusCode" in response and str(response["StatusCode"])[0] == "2":
            succ_msg = f"{{\"faasr_trigger\": \"Successfully invoked: {self.faasr['FunctionInvoke']}\"}}\n"
            print(succ_msg)
            faasr_log(self.faasr, succ_msg)
        else:
            try:
                err_msg = f"{{\"faasr_trigger\": \"Error invoking function: {self.faasr['FunctionInvoke']} -- error: {response['FunctionError']}\"}}\n"
                print(err_msg)
                faasr_log(self.faasr, err_msg)
            except Exception:
                err_msg = f"{{\"faasr_trigger\": \"Error invoking function: {self.faasr['FunctionInvoke']} -- no response from AWS\"}}\n"
                print(err_msg)
                faasr_log(self.faasr, err_msg)

    # to-do
    def invoke_ow(self, next_compute_server, function):
        """
        Trigger OpenWhisk function

        Arguments:
            next_compute_server: dict -- next compute server configuration
            function: str -- name of the function to invoke
        """
        # Get ow credentials
        endpoint = next_compute_server["Endpoint"]
        api_key = next_compute_server["API.key"]
        api_key = api_key.split(":")

        # Check if we should use ssl
        if "SSL" not in next_compute_server or not next_compute_server["SSL"]:
            ssl = True
        else:
            if next_compute_server["SSL"].lower() != "false":
                ssl = True
            else:
                ssl = False

        # Get the namespace of the OW server
        namespace = next_compute_server["Namespace"]
        actionname = function

        # Append https:// front to endpoint if needed
        if not endpoint.startswith("http"):
            endpoint = f"https://{endpoint}"

        # Create url for POST
        url = f"{endpoint}/api/v1/namespaces/{namespace}/actions/{actionname}?blocking=false&result=false"

        # Create headers for POST
        headers = {"accept": "application/json", "Content-Type": "application/json"}

        # to-do: invoke should take URL of payload & overwritten fields (not payload itself)
        # as input, and secrets if "UseSecretStore" is False for next_compute_server
        payload_dict = self.faasr.get_complete_workflow()
        # Create body for POST
        json_payload = json.dumps(payload_dict)

        # Issue POST request
        try:
            response = requests.post(
                url=url,
                auth=(api_key[0], api_key[1]),
                data=json_payload,
                headers=headers,
                verify=ssl,
            )
        except requests.exceptions.ConnectionError:
            err_msg = f"{{faasr_trigger: OpenWhisk: Error invoking {self.faasr['FunctionInvoke -- connection error']}}}"
            print(err_msg)
            sys.exit(1)
        except Exception as e:
            err_msg = f"{{\"faasr_trigger\": \"OpenWhisk: Error invoking {self.faasr['FunctionInvoke']} -- see logs\"}}"
            nat_err_msg = f"{{\"faasr_trigger\": \"OpenWhisk: Error invoking {self.faasr['FunctionInvoke']} -- error: {e}\"}}"
            print(err_msg)
            faasr_log(self.faasr, nat_err_msg)
            sys.exit(1)

        if response.status_code == 200 or response.status_code == 202:
            succ_msg = f"{{\"faasr_trigger\":\"OpenWhisk: Succesfully invoked {self.faasr['FunctionInvoke']}\"}}"
            print(succ_msg)
            faasr_log(self.faasr, succ_msg)
        else:
            err_msg = f"{{\"faasr_trigger\":\"OpenWhisk: Error invoking {self.faasr['FunctionInvoke']} -- status code: {response.status_code}\"}}"
            print(err_msg)
            faasr_log(self.faasr, err_msg)


def contains_dict(list_obj):
    """
    Returns true if list contains dict
    """
    if not isinstance(list_obj, list):
        return False

    for element in list_obj:
        if isinstance(element, dict):
            return True
    return False
