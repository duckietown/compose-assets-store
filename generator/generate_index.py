#!/usr/bin/env python3

import os
import re
import sys
import json
import argparse
import logging
import requests
from collections import defaultdict

logging.basicConfig()
logger = logging.getLogger('index-generator')
logger.setLevel(logging.DEBUG)

GIT_PROVIDER_TO_TAGS_URL = {
    "github.com": "https://api.github.com/repos/{git_owner}/{git_repository}/git/refs/tags",
    "bitbucket.com": "NOT_SUPPORTED_YET"
}

GIT_PROVIDER_TO_RAW_URL = {
    "github.com": "https://raw.githubusercontent.com/{git_owner}/{git_repository}/{tag}/{object}",
    "bitbucket.com": "NOT_SUPPORTED_YET"
}

NO_COMPATIBILITY_DATA = {
    'compose': {
        'minimum': 'v0.0.0',
        'maximum': 'v0.9.9',
    }
}

VERSION_REGEX = r"^v[\d]+\.[\d]+\.[\d]+$"


def main():
    # configure arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--index', required=True,
                        help="Index file to write to")
    parser.add_argument('--packages', required=True,
                        help="File containing the list of packages to create the index for")
    parser.add_argument('--no-cache', default=False, action='store_true',
                        help="Disable cache")
    parsed, _ = parser.parse_known_args()
    # ---
    parsed.index = os.path.abspath(parsed.index)
    parsed.packages = os.path.abspath(parsed.packages)
    # read list of packages
    with open(parsed.packages, 'r') as fin:
        data = json.load(fin)
        packages = data['packages']
    # load eTags
    cache = defaultdict(lambda: None)
    cache_file = None
    if not parsed.no_cache:
        cache_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'cache.json')
        try:
            with open(cache_file, 'r') as fin:
                cache.update(json.load(fin))
        except BaseException:
            pass
    # prepare index
    index = {
        'packages': {}
    }
    if not parsed.no_cache and os.path.isfile(parsed.index):
        with open(parsed.index, 'rt') as fin:
            index = json.load(fin)
    # check which configurations are valid
    stats = {
        'cache': {
            'hits': 0,
            'misses': 0
        },
        'num_packages': 0
    }
    logger.info('Found {:d} repositories.'.format(len(packages)))
    for package in packages:
        logger.info('Analyzing [{:s}]'.format(package['id']))
        cached_repo = cache[package['id']]
        tags_url = GIT_PROVIDER_TO_TAGS_URL[package['git_provider']].format(**package)
        # call API
        logger.info('> Fetching list of tags')
        response = requests.get(
            tags_url,
            headers={'If-None-Match': cached_repo['ETag']} if cached_repo else {},
            timeout=10
        )
        # check quota
        if response.status_code == 401 and response.headers['X-RateLimit-Remaining'] == 0:
            logger.error('API quota exhausted! Exiting.')
            sys.exit(1)
        # check output
        if response.status_code == 404:
            logger.error('< Repository "{id}" not found'.format(**package))
            sys.exit(2)
        # update cache
        if response.status_code == 200:
            logger.info('< Fetched from {git_provider}.'.format(**package))
            stats['cache']['misses'] += 1
            # noinspection PyTypeChecker
            cache[package['id']] = {
                'ETag': response.headers['ETag'],
                'Content': response.json()
            }
            if not parsed.no_cache:
                with open(cache_file, 'w') as fout:
                    json.dump(cache, fout, indent=4, sort_keys=True)
        # cached content
        if response.status_code == 304:
            stats['cache']['hits'] += 1
            logger.info('< Using cached data.')
        # get json response
        tags = [b['ref'].split('/')[-1] for b in cache[package['id']]['Content']]
        # populate index
        if package['id'] not in index['packages']:
            index['packages'][package['id']] = {
                'git': {
                    'provider': package['git_provider'],
                    'owner': package['git_owner'],
                    'repository': package['git_repository']
                },
                'versions': {},
                'icon': package['icon']
            }
        # fetch compatibility data
        for tag in tags:
            if not re.match(VERSION_REGEX, tag):
                logger.debug('  ! Ignoring tag {tag}, not a valid version'.format(tag=tag))
                continue
            logger.info('   > Fetching data for tag {tag}'.format(tag=tag))
            # check if we already have this data
            if tag in index['packages'][package['id']]['versions']:
                logger.info('   < Using cached data')
                stats['cache']['hits'] += 1
                continue
            stats['cache']['misses'] += 1
            # fetch data from the provider
            metadata_url = GIT_PROVIDER_TO_RAW_URL[package['git_provider']].format(
                tag=tag, object='metadata.json', **package
            )
            # call API
            response = requests.get(metadata_url, timeout=10)
            # check output
            if response.status_code == 404:
                logger.error("   < ERROR: Could not fetch object 'metadata.json'")
                continue
            # on success
            metadata = {}
            if response.status_code == 200:
                logger.info('   < Fetched from {git_provider}.'.format(**package))
                metadata = response.json()
            # check data
            if len(metadata) == 0:
                logger.error("   < ERROR: Could not decode stream for 'metadata.json'")
                continue
            # populate index
            index['packages'][package['id']].update({
                'name': metadata['name'],
                'description': metadata['description']
            })
            # prepare new version
            version_info = {
                'dependencies': metadata['dependencies']['packages']
            }
            # get compatibility data
            if 'compatibility' in metadata and 'compose' in metadata['compatibility']:
                version_info['compatibility'] = metadata['compatibility']
            else:
                version_info['compatibility'] = NO_COMPATIBILITY_DATA
            # store version
            index['packages'][package['id']]['versions'][tag] = version_info

        stats['num_packages'] += 1
    # write index
    with open(parsed.index, 'wt') as fout:
        json.dump(index, fout, sort_keys=True, indent=4)
    # print out stats
    logger.info(
        'Statistics:\n\tNum packages:  {:d}\n\tCache[Hits]:   {:d}\n\tCache[Misses]: {:d}'.format(
            stats['num_packages'], stats['cache']['hits'], stats['cache']['misses']
        )
    )
    logger.info('Done!')


if __name__ == '__main__':
    main()
