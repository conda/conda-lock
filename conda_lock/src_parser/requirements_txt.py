"""
Parses pip requirements from a requirements file
"""
from pip._internal.network.session import PipSession
from pip._internal.req.req_file import parse_requirements
from pip._internal.req.req_file import ParsedRequirement


def parse_requirements_txt(file_path):
    session = PipSession()
    return parse_requirements(file_path, session)
