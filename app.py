import docker
import os
import posixpath
import tarfile
import tempfile

from config import *
from flask import Flask, request
from io import BytesIO
from time import time

app = Flask(__name__)
client = docker.from_env()

@app.route("/languages")
def languages():
  return {
    "languages": VALID_LANGUAGES
  }

@app.route("/run", methods=["POST"])
def run():
  json = request.get_json()

  code = json.get("code")

  if not code:
    return {
      "error": {
        "message": "Missing code input."
      }
    }, 400

  language = json.get("language")

  if not language:
    return {
      "error": {
        "message": "Missing language."
      }
    }, 400

  if not language in VALID_LANGUAGES:
    return {
      "error": {
        "message": "Invalid language."
      }
    }, 400

  tar_archive = put_file_into_tar(f"{UNTRUSTED_CODE_FILENAME}.{language}", code)

  output = run_untrusted_code(language, tar_archive)

  os.remove(tar_archive)

  return output

def extract_tar_into_container(container, source, destination):
  contents = open(source, "rb").read()
  container.put_archive(destination, contents)

def put_file_into_tar(filename, content):
  archive_name = str(time()) + ".tar"
  tar_path = posixpath.join(tempfile.gettempdir(), archive_name)

  tar_info = tarfile.TarInfo(filename)
  tar_info.size = len(content)

  tar_file = tarfile.open(tar_path, "w")

  tar_file.addfile(tar_info, BytesIO(content.encode("utf8")))
  tar_file.close()

  return tar_path

def run_command_in_container(container, cmd):
  exit_code, output = container.exec_run(cmd, demux=True, privileged=False)
  stdout, stderr = output

  return {
    "exit_code": exit_code,
    "stderr": stderr.decode("utf-8") if stderr else "",
    "stdout": stdout.decode("utf-8") if stdout else ""
  }

# TODO: kill containers that have been running for > x seconds
def run_untrusted_code(language, tar_path):
  container = client.containers.run(
    auto_remove=True,
    command=CONTAINER_TERMINAL,
    cpu_shares=512,
    detach=True,
    image=CONTAINER_IMAGE,
    mem_limit=CONTAINER_MAX_MEMORY,
    network_disabled=not CONTAINER_NETWORKING_ENABLED,
    remove=True,
    runtime=CONTAINER_RUNTIME,
    stderr=True,
    stdout=True,
    tty=True
  )

  extract_tar_into_container(container, tar_path, UNTRUSTED_CODE_DIRECTORY)

  executable_path = posixpath.join(UNTRUSTED_CODE_DIRECTORY, UNTRUSTED_CODE_FILENAME)

  # if the language requires the code to be compiled, then compile it
  compiler_output = None

  if language == "c":
    compiler_output = run_command_in_container(container, f"gcc {executable_path}.c -o {executable_path}")
  elif language == "cpp":
    compiler_output = run_command_in_container(container, f"g++ {executable_path}.cpp -o {executable_path}")

  # now run the program
  program_output = None

  if language == "c" or language == "cpp":
    program_output = run_command_in_container(container, f"{executable_path}")
  elif language == "js":
    program_output = run_command_in_container(container, f"node {executable_path}.js")
  elif language == "py":
    program_output = run_command_in_container(container, f"python {executable_path}.py")

  # kill the container for us if the code did not hang
  container.kill()
  
  return {
    "compiler_output": compiler_output,
    "program_output": program_output
  }

  