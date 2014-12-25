#!/usr/bin/env python
# encoding: utf-8
import logging
import json
import os
import shutil
import git
import re

from collections import defaultdict
from crew.worker import context, HandlerClass
from uuid import uuid4
from tempfile import gettempdir

log = logging.getLogger("builder")


class TempoaryFolder(object):
    def __init__(self):
        self._dir = os.path.join(gettempdir(), str(uuid4()))
        self._curdir = os.path.abspath(os.getcwd())

    def __enter__(self):
        assert not os.path.exists(self._dir)

        log.debug('Making directory: "%s"', self._dir)
        os.makedirs(self._dir)

        log.debug('Changing directory: "%s"', self._dir)
        os.chdir(self._dir)
        return self._dir

    def __exit__(self, exc_type, exc_val, exc_tb):
        log.debug('Changing directory: "%s"', self._curdir)
        os.chdir(self._curdir)

        log.debug('Deleting directory: "%s"', self._dir)
        shutil.rmtree(self._dir)


class BuildHandler(HandlerClass):
    STREAM_EXPR = {
        "build_success": re.compile("^Successfully built\s+(?P<id>\S+)\n?$")
    }

    def process(self):
        self.git = git.Git()
        self.docker = context.settings.docker
        self.images = defaultdict(lambda: defaultdict(set))

        with TempoaryFolder() as path:
            self.prepare(path)
            self.get_images()
            try:
                self.data.update({"id": self.build(path)})
            except Exception as e:
                self.data.update({'error': e})

            self.data.update({'build_log': self.build_log})

        return self.data

    def prepare(self, path):
        url = self.data['repo']
        log.info('Cloning repo "%s" => "%s"', url, path)
        self.git.clone(url, path)

        commit_hash = self.data['commit']
        log.info('Checkout commit "%s"', commit_hash)
        self.git.checkout(commit_hash)

        log.info("Preparing complete")

    def get_images(self):
        for img in self.docker.images():
            for tag in img['RepoTags']:
                t = tag.split(":")
                if len(t) > 1:
                    repo, rtag = t
                else:
                    repo, rtag = t[0], None

                self.images[repo][rtag].add(img['Id'])

    def build(self, path):
        tag = "%s:%s" % (self.data['name'], self.data['tag'].lstrip("v"))
        self.build_log = []
        for line in self.docker.build(path, pull=True, rm=True, forcerm=True, tag=tag):
            chunk = json.loads(line)
            stream = chunk.get("stream", "").rstrip("\n\r")
            if stream:
                success = self.STREAM_EXPR['build_success'].match(stream)
                self.build_log.append(stream)
                if success:
                    return success.groupdict()['id']
                else:
                    log.info(stream)

            elif chunk.get("error"):
                err = chunk['error'].strip("\n\r")
                log.error(err)
                self.build_log.append(err)
                raise StandardError(chunk['error'])

    def resolve_image_id(self, image_id):
        self.docker.images()


BuildHandler.bind("build")