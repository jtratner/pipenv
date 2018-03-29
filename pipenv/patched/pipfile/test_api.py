import pytest

txt = """
[[source]]

url = "https://pypi.counsyl.com/jtratnertestproject/dev/+simple"
verify_ssl = true
name = "pypi"

[packages]

### Science Requirements ###

"numpy" = "==1.13.1"  # Changelog / releases: https://github.com/numpy/numpy/releases
"""
import api

expected_txt = txt + '\n"requests" = "*"'


def test_roundtripping(tmpdir):
    pipfile = tmpdir.join('Pipfile')
    pipfile.write(txt)
    pfile = api.load(str(pipfile))
    pfile = pfile.add_requirement('requests', version='*')


class TestPackageRequirement(object):
    def test_requirement_short_form(self):
        r = api.PackageRequirement.from_json('requests', '*')
        assert r.version == '*'
        assert r.name == 'requests'
        assert not r.extras

    def test_requirement_with_vcs(self):
        r = api.PackageRequirement.from_json(
            'unittest2',
            {'git': 'https://github.com/whatever',
            'ref': 'something'})
        assert r.vcs.vcs_name == 'git'
        assert r.vcs.ref == 'something'

    def test_requirement_back_compat(self):
        r = api.PackageRequirement.from_json(
            'name',
            {'version': 'blah', 'extrakey': 'another'})
        assert r.version == 'blah'
        assert r._data['extrakey'] == 'another'

    def test_requirement_multiple_vcs(self):
        with pytest.raises(ValueError, regex='saw multiple vcs keys'):
            api.PackagRequirement.from_json(
                'random',
                {'git': 'https://whatever.com', 'svn': 'another.com'})

