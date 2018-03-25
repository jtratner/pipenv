import toml

import codecs
import json
import hashlib
import platform
import six
import sys
import os
import attr


def format_full_version(info):
    version = '{0.major}.{0.minor}.{0.micro}'.format(info)
    kind = info.releaselevel
    if kind != 'final':
        version += kind[0] + str(info.serial)
    return version


def walk_up(bottom):
    """mimic os.walk, but walk 'up' instead of down the directory tree.
    From: https://gist.github.com/zdavkeos/1098474
    """

    bottom = os.path.realpath(bottom)

    # get files in current dir
    try:
        names = os.listdir(bottom)
    except Exception:
        return

    dirs, nondirs = [], []
    for name in names:
        if os.path.isdir(os.path.join(bottom, name)):
            dirs.append(name)
        else:
            nondirs.append(name)

    yield bottom, dirs, nondirs

    new_path = os.path.realpath(os.path.join(bottom, '..'))

    # see if we are at the top
    if new_path == bottom:
        return

    for x in walk_up(new_path):
        yield x


class PipfileParser(object):
    def __init__(self, filename='Pipfile'):
        self.filename = filename
        self.sources = []
        self.groups = {
            'default': [],
            'develop': []
        }
        self.group_stack = ['default']
        self.requirements = []

    def __repr__(self):
        return '<PipfileParser path={0!r}'.format(self.filename)

    def inject_environment_variables(self, d):
        """
        Recursively injects environment variables into TOML values
        """

        if not d:
            return d
        if isinstance(d, six.string_types):
            return os.path.expandvars(d)
        for k, v in d.items():
            if isinstance(v, six.string_types):
                d[k] = os.path.expandvars(v)
            elif isinstance(v, dict):
                d[k] = self.inject_environment_variables(v)
            elif isinstance(v, list):
                d[k] = [self.inject_environment_variables(e) for e in v]

        return d

    def parse(self, inject_env=True):
        # Open the Pipfile.
        with open(self.filename) as f:
            content = f.read()

        # Load the default configuration.
        default_config = {
            u'source': [{u'url': u'https://pypi.python.org/simple', u'verify_ssl': True, 'name': "pypi"}],
            u'packages': {},
            u'requires': {},
            u'dev-packages': {}
        }

        config = {}
        config.update(default_config)

        # Deserialize the TOML, and parse for Environment Variables
        parsed = toml.loads(content)

        if inject_env:
            injected_toml = self.inject_environment_variables(parsed)

            # Load the Pipfile's configuration.
            config.update(injected_toml)
        else:
            config.update(parsed)

        # Structure the data for output.
        data = {
            '_meta': {
                'sources': config['source'],
                'requires': config['requires']
            },
        }

        # TODO: Validate given data here.
        self.groups['default'] = config['packages']
        self.groups['develop'] = config['dev-packages']

        # Update the data structure with group information.
        data.update(self.groups)
        return data

@attr.s(frozen=True)
class Source(object):
    #: URL to PyPI instance
    url = attr.ib(default='')
    #: If False, skip SSL checks
    verify_ssl = attr.ib(default=True)
    #: human name to refer to this source (can be referenced in packages or dev-packages)
    name = attr.ib(default='')



@attr.s(frozen=True)
class Requires(object):
    """System-level requirements - see PEP508 for more detail"""
    os_name = attr.ib(default=None)
    sys_platform = attr.ib(default=None)
    platform_machine = attr.ib(default=None)
    platform_python_implementation = attr.ib(default=None)
    platform_release = attr.ib(default=None)
    platform_system = attr.ib(default=None)
    platform_version = attr.ib(default=None)
    python_version = attr.ib(default=None)
    python_full_version = attr.ib(default=None)
    implementation_name = attr.ib(default=None)
    implementation_version = attr.ib(default=None)

@attr.s(frozen=True)
class VCSRequirement(object):
    vcs_name = attr.ib()
    #: vcs reference name (branch / commit / tag)
    ref = attr.ib(default=None)
    #: path to hit - without any of the VCS prefixes (like git+ / http+ / etc)
    uri = attr.ib(default=None)
    subdirectory = attr.ib(default=None)

    @classmethod
    def split_requirement_dict(cls, dct):
        """Returns (Optional[VCSRequirement], extra_params)"""
        dct = dict(dct)
        vcs_dict = {}
        for vcs_key in  ('git', 'svn', 'hg', 'bzr'):
            uri = dct.pop(vcs_key, None)
            if uri:
                if vcs_dict:
                    raise ValueError('saw multiple vcs keys!')
                vcs_dict['vcs_name'] = vcs_key
                vcs_dict['uri'] = uri
        for key in ('ref', 'subdirectory'):
            vcs_dict[key] = dct.pop(key)
        return cls(**vcs_dict), dct


@attr.s(frozen=True)
class PackageRequirement(object):
    #: pypi name (internally normalized via something like, e.g., pkg_resources.safe_name)
    name = attr.ib(default=None)
    #: extra requirements - see pip / setuptools docs for more
    extras = attr.ib(default=tuple())
    specs = attr.ib(default=None)
    editable = attr.ib(default=False)
    vcs = attr.ib(default=None)
    # "specs" in pip requirement
    version = attr.ib(default=None)
    markers = attr.ib(default=None)

    def to_json(self):
        dct = attr.asdict(self)
        vcs = dct.pop('vcs')
        dct.pop('name')
        if vcs:
            dct.update(vcs.to_json())
        return {k: v for k, v in dct.items() if v is not None}

    @classmethod
    def from_json(cls, name, data):
        # TODO: make API less weird
        data = dict(data)
        vcs, my_data = VCSRequirement.split_requirement_dict(data)
        my_data['name'] = name
        my_data['vcs'] = vcs
        return cls(**my_data)


class LockedRequirement(PackageRequirement):
    hashes = attr.ib()


@attr.s
class LockMeta(object):
    hash = attr.ib()  # hashname => value
    host_environment_markers = attr.ib()  # Requires instance with *all* fields
    sources = attr.ib() # list of sources
    requires = attr.ib() # Requires instance with only subset of fields
    pipfile_spec = attr.ib(default=6)

    def to_json(self):
        return {
            'hash': self.hash,
            'host-environment-markers': attr.asdict(self.host_environment_markers),
            'pipfile-spec': self.pipfile_spec,
            'sources': [attr.asdict(s) for s in self.sources]
        }

    @classmethod
    def from_json(cls, dct):
        dct['host_environment_markers'] = Requires(**dct.pop('host-environment-markers'))
        dct['pipfile_spec'] = dct.pop('pipfile-spec')
        dct['requires'] = Requires(**dct['requires'])
        dct['sources'] = [Source(**s) for s in dct['sources']]
        return cls(**dct)



@attr.s
class LockedRequirementSet(object):
    requirements = attr.ib(default=tuple())

    def to_json(self):
        return { # TODO(use safe name here)
            r.name: r.to_json() for r in self.requirements}

    @classmethod
    def from_json(cls, dct):
        requirements = []
        for name, req in dct.items():
            req = dict(req)
            req['name'] = name
            # TODO: This needs to work!
            requirements.append(LockedRequirement.from_json(name, **req))


@attr.s
class LockedPipfile(object):
    default = attr.ib()  # LockedRequirementSet
    develop = attr.ib()  # LockedRequirementSet
    meta = attr.ib()

    def to_json(self):
        return {
            '_meta': self.meta.to_dict(),
            'default': self.default.to_json(),
            'develop': self.default.to_json()
        }

    @classmethod
    def from_json(cls, dct):
        """Generate from python dictionary that is loaded JSON"""
        meta = dct.pop('_meta')


@attr.s(frozen=True)
class RequirementSet(object):
    packages = attr.ib()


class _Pipfile(object):
    #: source filename
    filename = attr.ib()
    sources = attr.ib()
    packages = attr.ib()
    dev_packages = attr.ib()

class Pipfile(object):
    def __init__(self, filename):
        super(Pipfile, self).__init__()
        self.filename = filename
        self.data = None

    @staticmethod
    def find(max_depth=3):
        """Returns the path of a Pipfile in parent directories."""
        i = 0
        for c, d, f in walk_up(os.getcwd()):
            i += 1

            if i < max_depth:
                if 'Pipfile':
                    p = os.path.join(c, 'Pipfile')
                    if os.path.isfile(p):
                        return p
        raise RuntimeError('No Pipfile found!')

    @classmethod
    def load(klass, filename, inject_env=True):
        """Load a Pipfile from a given filename."""
        p = PipfileParser(filename=filename)
        pipfile = klass(filename=filename)
        pipfile.data = p.parse(inject_env=inject_env)
        return pipfile

    @property
    def hash(self):
        """Returns the SHA256 of the pipfile's data."""
        content = json.dumps(self.data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(content.encode("utf8")).hexdigest()

    @property
    def contents(self):
        """Returns the contents of the pipfile."""
        with codecs.open(self.filename, 'r', 'utf-8') as f:
            return f.read()

    def lock(self):
        """Returns a JSON representation of the Pipfile."""
        data = self.data
        data['_meta']['hash'] = {"sha256": self.hash}
        data['_meta']['pipfile-spec'] = 6
        return json.dumps(data, indent=4, separators=(',', ': '))

    def assert_requirements(self):
        """"Asserts PEP 508 specifiers."""

        # Support for 508's implementation_version.
        if hasattr(sys, 'implementation'):
            implementation_version = format_full_version(sys.implementation.version)
        else:
            implementation_version = "0"

        # Default to cpython for 2.7.
        if hasattr(sys, 'implementation'):
            implementation_name = sys.implementation.name
        else:
            implementation_name = 'cpython'

        lookup = {
            'os_name': os.name,
            'sys_platform': sys.platform,
            'platform_machine': platform.machine(),
            'platform_python_implementation': platform.python_implementation(),
            'platform_release': platform.release(),
            'platform_system': platform.system(),
            'platform_version': platform.version(),
            'python_version': platform.python_version()[:3],
            'python_full_version': platform.python_version(),
            'implementation_name': implementation_name,
            'implementation_version': implementation_version
        }

        # Assert each specified requirement.
        for marker, specifier in self.data['_meta']['requires'].items():

            if marker in lookup:
                try:
                    assert lookup[marker] == specifier
                except AssertionError:
                    raise AssertionError('Specifier {!r} does not match {!r}.'.format(marker, specifier))


def load(pipfile_path=None, inject_env=True):
    """Loads a pipfile from a given path.
    If none is provided, one will try to be found.
    """

    if pipfile_path is None:
        pipfile_path = Pipfile.find()

    return Pipfile.load(filename=pipfile_path, inject_env=inject_env)
