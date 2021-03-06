import re
import os
import shutil
import logging
from lxml import objectify


class FileService:
    """
    Handles downloading files from the LIMS and keeping local copies of them as
    well as cleaning up after a script as run
    """

    def __init__(self, artifact_service, file_repo, should_cache, os_service):
        """
        :param artifact_service: An artifact service instance.
        :param should_cache: Set to True if files should be cached in .cache, mainly
        for faster integration tests.
        """
        self._local_shared_files = []
        self.artifact_service = artifact_service
        self.logger = logging.getLogger(__name__)
        self.should_cache = should_cache
        self.file_repo = file_repo
        self.os_service = os_service

    def parse_xml(self, f):
        """
        Parses the file like object as XML and returns an object that provides simple access to
        the leaves, such as `parent.child.grandchild`
        """
        with f:
            tree = objectify.parse(f)
            return tree.getroot()

    def parse_csv(self, f):
        with f:
            return Csv(f)

    def local_shared_file(self, file_name, mode='r', extension="", modify_attached=False):
        """
        Downloads the local shared file and returns an open file-like object.

        If the file already exists, it will not be downloaded again.

        Details:
        The downloaded files will be removed when the context is cleaned up. This ensures
        that the LIMS will not upload them by accident
        """

        # TODO: Mockable, file system repo

        # Ensure that the user is only sending in a "name" (alphanumerical or spaces)
        # File paths are not allowed
        if not re.match(r"[\w ]+", file_name):
            raise ValueError(
                "File name can only contain alphanumeric characters, underscores and spaces")
        local_file_name = ".".join([file_name.replace(" ", "_"), extension])
        local_path = os.path.abspath(local_file_name)
        local_path = os.path.abspath(local_path)
        cache_directory = os.path.abspath(".cache")
        cache_path = os.path.join(cache_directory, local_file_name)
        artifact = None

        if self.should_cache and os.path.exists(cache_path):
            self.logger.info("Fetching cached artifact from '{}'".format(cache_path))
            shutil.copy(cache_path, ".")
        else:
            if not os.path.exists(local_path):
                artifact = self._artifact_by_name(file_name)

                if len(artifact.api_resource.files) == 0:
                    # No file has been uploaded yet
                    if modify_attached:
                        with open(local_path, "w+") as fs:
                            pass
                else:
                    file = artifact.api_resource.files[0]  # TODO: Hide this logic
                    self.logger.info("Downloading file {} (artifact={} '{}')"
                                     .format(file.id, artifact.id, artifact.name))
                    self.file_repo.copy_remote_file(file.id, local_path)
                    self.logger.info("Download completed, path='{}'".format(local_path))

                    if self.should_cache:
                        if not os.path.exists(cache_directory):
                            os.mkdir(cache_directory)
                        self.logger.info("Copying artifact to cache directory, {}=>{}".format(
                            local_path, cache_directory))
                        shutil.copy(local_path, cache_directory)

        # Add to this to the cleanup list
        if local_path not in self._local_shared_files:
            self._local_shared_files.append(local_path)

        if modify_attached:
            if artifact is None:
                artifact = self._artifact_by_name(file_name)
            # After this, the caller will be able to modify the file with the prefix that ensures
            # that this will be uploaded afterwards. We don't want that in the case of files that are
            # not to be modified, since then they would be automatically uploaded afterwards.
            attached_name = self.os_service.attach_file_for_epp(local_file_name, artifact.api_resource)
            local_path = attached_name

        return self.file_repo.open_local_file(local_path, mode)

    def _artifact_by_name(self, file_name):
        shared_files = self.artifact_service.shared_files()
        by_name = [shared_file for shared_file in shared_files
                   if shared_file.name == file_name]
        if len(by_name) != 1:
            files = ", ".join(map(lambda x: x.name, shared_files))
            raise SharedFileNotFound("Expected a shared file called '{}', got {}.\nFile: '{}'\nFiles: {}".format(
                file_name, len(by_name), file_name, files))
        artifact = by_name[0]
        return artifact

    def cleanup(self):
        for path in self._local_shared_files:
            if os.path.exists(path):
                self.logger.info("Local shared file '{}' will be removed to ensure "
                                 "that it won't be uploaded again".format(path))
                # TODO: Handle exception
                os.remove(path)


class SharedFileNotFound(Exception):
    pass


class Csv:
    """A simple wrapper for csv files"""
    def __init__(self, file_stream, delim=","):
        if isinstance(file_stream, basestring):
            with open(file_stream, "r") as fs:
                self._init_from_file_stream(fs, delim)
        else:
            self._init_from_file_stream(file_stream, delim)

    def _init_from_file_stream(self, file_stream, delim):
        lines = list()
        for line in file_stream:
            values = line.strip().split(delim)
            csv_line = CsvLine(values, self)
            lines.append(csv_line)
            if len(lines) == 1:
                self.key_to_index = {key: ix for ix, key in enumerate(values)}
        self.header = lines[0]
        self.data = lines[1:]

    def __iter__(self):
        return iter(self.data)


class CsvLine:
    """Represents one line in a CSV file, items can be added or removed like this were a dictionary"""
    def __init__(self, line, csv):
        self.line = line
        self.csv = csv

    def __getitem__(self, key):
        index = self.csv.key_to_index[key]
        return self.line[index]

    def __setitem__(self, key, value):
        index = self.csv.key_to_index[key]
        self.line[index] = value

    @property
    def values(self):
        return self.line

    def __repr__(self):
        return repr(self.values)

