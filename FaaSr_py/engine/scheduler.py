import json
import logging
import re
import sys

import boto3
import requests

from FaaSr_py.config.debug_config import global_config
from FaaSr_py.engine.faasr_payload import FaaSrPayload

logger = logging.getLogger(__name__)


class Scheduler:
    """
    Handles scheduling of next functions in the DAG
    """

    def __init__(self, faasr: FaaSrPayload):
        if not isinstance(faasr, FaaSrPayload):
            err_msg = "initializer for Scheduler must be FaaSrPayload instance"
            logger.error(err_msg)
            sys.exit(1)
        self.faasr = faasr

    def trigger_all(self, return_val=None):
        """
        Batch trigger for all the next actions in the DAG

        Arguments:
            return_val: any -- value returned by the user function, used for conditionals
        """
        # Get a list of the next functions to invoke
        curr_func = self.faasr["FunctionInvoke"]
        invoke_next = self.faasr["ActionList"][curr_func]["InvokeNext"]
        if not isinstance(invoke_next, list):
            invoke_next = [invoke_next]

        # If there is no more triggers, then return
        if not invoke_next:
            msg = f"no triggers for {curr_func}"
            logger.info(msg)
            return

        # Ensure that function returned a value if conditionals are present
        if contains_dict(invoke_next) and return_val is None:
            err_msg = (
                "InvokeNext contains conditionals but function did not return a value"
            )
            logger.error(err_msg)
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
        next_server = self.faasr["ActionList"][function]["FaaSServer"]

        if global_config.SKIP_REAL_TRIGGERS:
            logger.info("SKIPPING REAL TRIGGERS")

        for rank in range(1, rank_num + 1):
            if rank_num > 1:
                self.faasr["FunctionRank"] = rank  # add functionrank to overwritten
            else:
                if "FunctionRank" in self.faasr:
                    del self.faasr["FunctionRank"]

            if next_server not in self.faasr["ComputeServers"]:
                err_msg = f"invalid server name: {next_server}"
                logger.error(err_msg)
                sys.exit(1)

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
                    case "SLURM":
                        self.invoke_slurm(next_compute_server, function)
            else:
                msg = f"SIMULATED TRIGGER: {function}"
                if rank_num > 1:
                    msg += f".{rank}"
                logger.info(msg)

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

        # Create payload input
        overwritten_fields = self.faasr.overwritten

        # If UseSecretStore == True, don't send secrets to next action
        # Otherwise, we should send the compute servers & data stores
        # that contain secrets via overwritten
        if next_compute_server.get("UseSecretStore"):
            if "ComputeServers" in overwritten_fields:
                del overwritten_fields["ComputeServers"]
            if "DataStores" in overwritten_fields:
                del overwritten_fields["DataStores"]
        else:
            overwritten_fields["ComputeServers"] = self.faasr["ComputeServers"]
            overwritten_fields["DataStores"] = self.faasr["DataStores"]

        json_overwritten = json.dumps(overwritten_fields)

        inputs = {
            "OVERWRITTEN": json_overwritten,
            "PAYLOAD_URL": self.faasr.url,
        }

        # Create url for GitHub API
        url = (
            f"https://api.github.com/repos/"
            f"{repo}/actions/workflows/"
            f"{workflow_file}/dispatches"
        )

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
            succ_msg = (
                f"GitHub Action: Successfully invoked: {self.faasr['FunctionInvoke']}"
            )
            logger.info(succ_msg)
        elif response.status_code == 401:
            err_msg = "GitHub Action: Authentication failed, check the credentials"
            logger.error(err_msg)
            sys.exit(1)
        elif response.status_code == 404:
            err_msg = (
                f"GitHub Action: Cannot find the destination: "
                f"check repo: {repo}, workflow: {workflow_file}, "
                f"and branch: {git_ref}"
            )
            logger.error(err_msg)
            sys.exit(1)
        elif response.status_code == 422:
            message = response.json().get("message")
            if message:
                err_msg = f"GitHub Action: {message}"
            else:
                err_msg = (
                    f"GitHub Action': 'Cannot find the destination; check ref {git_ref}"
                )
            logger.error(err_msg)
            sys.exit(1)
        else:
            if response:
                message = response.json().get("message")
                if message:
                    err_msg = f"{message}"
                else:
                    err_msg = (
                        "GitHub Action: Unknown error happens when invoke next function"
                    )
                logger.error(err_msg)
                sys.exit(1)
            else:
                err_msg = "GitHub Action: No response from GitHub"
                logger.error(err_msg)
                sys.exit(1)

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

        overwritten_fields = self.faasr.overwritten

        # Don't send secrets to next action if UseSecretStore is set
        if next_compute_server.get("UseSecretStore"):
            if "ComputeServers" in overwritten_fields:
                del overwritten_fields["ComputeServers"]
            if "DataStores" in overwritten_fields:
                del overwritten_fields["DataStores"]
        else:
            overwritten_fields["ComputeServers"] = self.faasr["ComputeServers"]
            overwritten_fields["DataStores"] = self.faasr["DataStores"]

        try:
            payload = {
                "OVERWRITTEN": json.dumps(overwritten_fields),
                "PAYLOAD_URL": self.faasr.url,
            }

            response = lambda_client.invoke(
                FunctionName=function,
                InvocationType='Event',
                Payload=json.dumps(payload),
            )
        except Exception as e:
            logger.exception(e, stack_info=True)
            sys.exit(1)

        if "StatusCode" in response and str(response["StatusCode"])[0] == "2":
            succ_msg = f"Lambda: Successfully invoked: {self.faasr['FunctionInvoke']}"
            logger.info(succ_msg)
        else:
            try:
                err_msg = (
                    f"Error invoking function: {self.faasr['FunctionInvoke']} -- "
                    f"{response['FunctionError']}"
                )
                logger.error(err_msg)
            except Exception:
                err_msg = f"Error invoking function: {self.faasr['FunctionInvoke']}"
                logger.exception(err_msg, stack_info=True)
            sys.exit(1)

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
        url = (
            f"{endpoint}/api/v1/namespaces/{namespace}/actions/"
            f"{actionname}?blocking=false&result=false"
        )

        # Create headers for POST
        headers = {"accept": "application/json", "Content-Type": "application/json"}

        # to-do:
        # invoke should take URL of payload & overwritten fields (not payload itself)
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
            logger.exception(stack_info=True)
            sys.exit(1)
        except Exception:
            err_msg = f"OpenWhisk: Error invoking {self.faasr['FunctionInvoke']}"
            logger.exception(err_msg, stack_info=True)
            sys.exit(1)

        if response.status_code == 200 or response.status_code == 202:
            succ_msg = f"OpenWhisk: Succesfully invoked {self.faasr['FunctionInvoke']}"
            logger.info(succ_msg)
            sys.exit(1)
        else:
            err_msg = (
                f"OpenWhisk: Error invoking {self.faasr['FunctionInvoke']}: "
                f"status code: {response.status_code}"
            )
            logger.error(err_msg)
            sys.exit(1)

    def invoke_slurm(self, next_compute_server, function):
        """
        Trigger SLURM job for next function
        Follows the same pattern as GitHub Actions with URL + overwritten fields + secrets

        Arguments:
            next_compute_server: dict -- next compute server configuration
            function: str -- name of the function to invoke
        """

        from FaaSr_py.helpers.slurm_helper import (
            create_job_script,
            get_resource_requirements,
            make_slurm_request,
            validate_jwt_token,
        )

        # Get server configuration
        server_info = next_compute_server
        api_version = server_info.get("APIVersion", "v0.0.37")
        endpoint = server_info["Endpoint"]

        # Ensure endpoint has protocol
        if not endpoint.startswith("http"):
            endpoint = f"http://{endpoint}"

        # Validate JWT token (same validation as R package)
        token = server_info.get("Token")
        if not token or token.strip() == "":
            err_msg = f"SLURM: No authentication token available for server {function}"
            logger.error(err_msg)
            sys.exit(1)

        # Validate JWT token
        token_validation = validate_jwt_token(server_info.get("Token"))
        if not token_validation["valid"]:
            err_msg = (
                f"SLURM: Token validation failed for {self.faasr['FunctionInvoke']} - "
                f"{token_validation['error']}"
            )
            logger.error(err_msg)
            sys.exit(1)

        # Validate username configuration
        username = server_info.get("UserName", "ubuntu")
        if not username:
            err_msg = f"SLURM: Username not configured for server {function}"
            logger.error(err_msg)
            sys.exit(1)

        # Create overwritten fields for the next action (following GitHub Actions pattern)
        overwritten_fields = self.faasr.overwritten.copy()

        if next_compute_server.get("UseSecretStore"):
            # Next action will fetch secrets from its own secret store
            # Remove secrets from overwritten fields
            if "ComputeServers" in overwritten_fields:
                del overwritten_fields["ComputeServers"]
            if "DataStores" in overwritten_fields:
                del overwritten_fields["DataStores"]
            logger.info(
                "Next SLURM action will use secret store. Secrets not included in payload"
            )
        else:
            # Next action expects secrets in payload
            # Include full ComputeServers and DataStores with secrets
            overwritten_fields["ComputeServers"] = self.faasr["ComputeServers"]
            overwritten_fields["DataStores"] = self.faasr["DataStores"]
            logger.info(
                "Next SLURM action expects secrets in payload - including credentials"
            )

        # Prepare environment variables for SLURM job
        environment_vars = {
            "PAYLOAD_URL": self.faasr.url,  # URL to GitHub-hosted workflow JSON
            "OVERWRITTEN": json.dumps(overwritten_fields, separators=(",", ":")),
        }

        # Create job script
        job_script = create_job_script(self.faasr, function, environment_vars)

        # Get resource requirements for the function
        resource_config = get_resource_requirements(self.faasr, function, server_info)

        # Add secrets only if UseSecretStore is False for current server
        current_func = self.faasr["FunctionInvoke"]
        current_server = self.faasr["ActionList"][current_func]["FaaSServer"]
        current_compute_server = self.faasr["ComputeServers"][current_server]

        if not current_compute_server.get("UseSecretStore"):
            # Include secrets for the next action (it doesn't have access to secret store)
            secrets_payload = {
                "ComputeServers": self.faasr["ComputeServers"],
                "DataStores": self.faasr["DataStores"],
            }
            environment_vars["SECRET_PAYLOAD"] = json.dumps(
                secrets_payload, separators=(",", ":")
            )

        else:
            logger.info(
                "Current server uses secret store - secrets will be fetched by SLURM job"
            )

        # Prepare job payload with resource requirements
        job_payload = {
            "job": {
                "name": f"faasr-{function}",
                "partition": resource_config["partition"],
                "nodes": str(resource_config["nodes"]),
                "tasks": str(resource_config["tasks"]),
                "cpus_per_task": str(resource_config["cpus_per_task"]),
                "memory_per_cpu": str(resource_config["memory_mb"]),
                "time_limit": str(resource_config["time_limit"]),
                "current_working_directory": resource_config["working_dir"],
                "environment": environment_vars,
            },
            "script": job_script,
        }

        # Submit job
        submit_url = f"{endpoint}/slurm/{api_version}/job/submit"

        logger.info(f"SLURM: Submitting job to {submit_url}")

        try:
            response = make_slurm_request(
                endpoint=submit_url,
                method="POST",
                headers=None,
                body=job_payload,
                token=token,
                username=username,
            )

            if response.status_code in [200, 201, 202]:
                job_info = response.json()

                # Extract job ID with multiple fallback options
                job_id = (
                    job_info.get("job_id")
                    or job_info.get("jobId")
                    or job_info.get("id")
                    or (
                        job_info.get("job", {}).get("job_id")
                        if job_info.get("job")
                        else None
                    )
                    or "unknown"
                )

                succ_msg = (
                    f"SLURM: Successfully submitted job: {self.faasr['FunctionInvoke']} "
                    f"(Job ID: {job_id})"
                )
                logger.info(succ_msg)
            else:
                error_content = response.text
                err_msg = (
                    f"SLURM: Error submitting job: {self.faasr['FunctionInvoke']} - "
                    f"HTTP {response.status_code}: {error_content}"
                )
                logger.error(err_msg)

                if response.status_code == 401:
                    logger.error(
                        "SLURM: Authentication failed - check token validity and username"
                    )
                elif response.status_code == 403:
                    logger.error("SLURM: Authorization failed - check user permissions")

                sys.exit(1)

        except ValueError as e:
            if "authentication" in str(e).lower():
                logger.error("SLURM: Authentication error - check your JWT token")
            logger.error(f"SLURM: Request error: {e}")
            sys.exit(1)
        except Exception as e:
            logger.exception(f"SLURM request failed: {e}")
            sys.exit(1)


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
