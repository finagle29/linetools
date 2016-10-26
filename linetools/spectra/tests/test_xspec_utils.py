# Module to run tests on spectra.io
from __future__ import print_function, absolute_import, \
     division, unicode_literals
import os
import pytest
from astropy import units as u
import numpy as np

from linetools.spectra import io
from linetools.spectra.xspectrum1d import XSpectrum1D
from linetools.spectra import utils as ltsu

@pytest.fixture
def spec():
    return io.readspec(data_path('UM184_nF.fits'))

@pytest.fixture
def spec2():
    return io.readspec(data_path('PH957_f.fits'))

@pytest.fixture
def specm(spec,spec2):
    return XSpectrum1D.from_list([spec,spec2])

def data_path(filename):
    data_dir = os.path.join(os.path.dirname(__file__), 'files')
    return os.path.join(data_dir, filename)


def test_collate(spec,spec2):
    coll_spec = ltsu.collate([spec,spec2])
    assert coll_spec.nspec == 2
    assert coll_spec.totpix == 20379


def test_rebin_to_rest(spec,spec2):
    zarr = np.array([2.1,2.2])
    # Build spectra array
    coll_spec = ltsu.collate([spec,spec2])
    rest_spec = ltsu.rebin_to_rest(coll_spec, zarr, 100*u.km/u.s, debug=False)
    # Test
    assert rest_spec.totpix == 3716
    np.testing.assert_allclose(rest_spec.wvmin.value, 986.021738877745, rtol=1e-5)


def test_smash_spectra(spec,spec2):
    # Try to stack 2 spectra with different wavelengths
    coll_spec = ltsu.collate([spec,spec2])
    with pytest.raises(AssertionError):
        stack = ltsu.smash_spectra(coll_spec)
    # Stack rebinned
    zarr = np.array([2.1,2.2])
    rest_spec = ltsu.rebin_to_rest(coll_spec, zarr, 100*u.km/u.s, debug=False)
    stack = ltsu.smash_spectra(rest_spec, method='average')
    # Test
    assert stack.totpix == 3716
    np.testing.assert_allclose(stack.flux[0].value, -1.19753563, rtol=1e-5)


def test_airtovac_andback(spec):
    npix = 1000
    spec = XSpectrum1D.from_tuple((np.linspace(5000.,6000,npix), np.ones(npix)))
    # Airtovac
    spec.meta['airvac'] = 'air'
    spec.airtovac()
    # Test
    np.testing.assert_allclose(spec.wavelength[0].value, 5001.394869990007, rtol=1e-5)
    assert spec.meta['airvac'] == 'vac'
    # Vactoair
    spec.vactoair()
    np.testing.assert_allclose(spec.wavelength[0].value, 5000., rtol=1e-5)
    assert spec.meta['airvac'] == 'air'


def test_rebin(spec):
    # Rebin
    new_wv = np.arange(3000., 9000., 5) * u.AA
    newspec = spec.rebin(new_wv)
    # Test
    np.testing.assert_allclose(newspec.flux[1000], 0.9999280967617779)
    assert newspec.flux.unit is u.dimensionless_unscaled
    # With sigma
    newspec = spec.rebin(new_wv, do_sig=True)
    """
    i1 = np.argmin(np.abs(spec.wavelength-5000.*u.AA))
    s2n_1 = spec.flux[i1] / spec.sig[i1]
    i2 = np.argmin(np.abs(newspec.wavelength-5000.*u.AA))
    s2n_2 = newspec.flux[i2] / newspec.sig[i2]
    """
    imn = np.argmin(np.abs(newspec.wavelength-8055*u.AA))
    np.testing.assert_allclose(newspec.sig[imn].value, 0.0169634, rtol=1e-5)
    # With NANs
    spec.data['flux'][spec.select][100:110] = np.nan
    newspec = spec.rebin(new_wv)
    np.testing.assert_allclose(newspec.flux[1000], 0.9999280967617779)


def test_addnoise(spec):
    #
    newspec = spec.add_noise(seed=12)
    np.testing.assert_allclose(newspec.flux[1000].value, 0.6003435, rtol=1e-5)

    # With S/N input
    newspec2 = spec.add_noise(seed=19,s2n=10.)
    np.testing.assert_allclose(newspec2.flux[1000].value, -0.130012, rtol=1e-5)


def test_box_smooth(spec):
    # Smooth
    newspec3 = spec.box_smooth(3)
    np.testing.assert_allclose(newspec3.flux[4000], 0.9650185, rtol=1e-5)
    assert newspec3.flux.unit == u.dimensionless_unscaled

    newspec5 = spec.box_smooth(5)
    np.testing.assert_allclose(newspec5.flux[3000], 1.0405008,rtol=1e-5)
    # Preserve
    newspec5p = spec.box_smooth(5, preserve=True)


def test_gauss_smooth(spec):
    # Smooth
    smth_spec = spec.gauss_smooth(4.)
    # Test
    np.testing.assert_allclose(smth_spec.flux[3000].value, 0.749937, rtol=1e-5)
    assert smth_spec.flux.unit == spec.flux.unit


def test_ivar_smooth(spec):
    # Smooth
    smth_spec = spec.ivar_smooth(4)
    # Test
    np.testing.assert_allclose(smth_spec.flux[3000].value, 0.7220675349235535, rtol=1e-5)
    assert smth_spec.flux.unit == spec.flux.unit


def test_rebintwo(spec):
    # Add units
    funit = u.erg/u.s/u.cm**2
    spec.units['flux'] = funit
    # Rebin
    new_wv = np.arange(3000., 9000., 5) * u.AA
    newspec = spec.rebin(new_wv, do_sig=True)
    # Test
    np.testing.assert_allclose(newspec.flux[1000].value, 1.0192499, rtol=1e-5)
    assert newspec.flux.unit == funit
    # Without sig
    spec_nosig = XSpectrum1D.from_tuple((spec.wavelength, spec.flux))
    newspec = spec.rebin(new_wv)
    assert newspec.sig_is_set is False


def test_relvel(spec):

    # Velocity
    velo = spec.relative_vel(5000.*u.AA)
    # Test
    np.testing.assert_allclose(velo[6600].value, -2322.625, rtol=1e-5)
    assert velo.unit == (u.km/u.s)


def test_splice_two(spec, spec2):
    spec3 = ltsu.splice_two(spec, spec2)
    assert spec3.npix == 18390

def test_stitch(specm):
    spec = specm.stitch()
    assert spec.npix == 18390


def test_copy(spec):
    # From existing
    spec2 = spec.copy()
    assert spec.wavelength[0] == spec2.wavelength[0]
    assert spec.flux[-1] == spec2.flux[-1]
    #
    wave = np.arange(3000., 6500)
    npix = len(wave)
    spect = XSpectrum1D.from_tuple((wave*u.AA,np.ones(npix)))
    specf = spect.copy()
    assert specf.sig_is_set is False


def test_plot(spec):
    spec.plot(show=False)


def test_continuum_utils(spec):
    # define continuum in a non-interactive way...
    n = len(spec.wavelength.value)
    contpoints = [(spec.wavelength.value[i], 1.) for i in range(n)[::int(n/100)]]
    spec.meta['contpoints'] = contpoints
    xy = np.array(spec.meta['contpoints'])
    xy = xy.transpose()
    x, y = xy[0], xy[1]
    # test interpolate
    spec.normalize(spec._interp_continuum(x, y, spec.wavelength.value))
    np.testing.assert_allclose(spec.co, 1.)
    co_old = spec.co
    # test perturb
    spec.perturb_continuum(rel_var=0.05, seed=2)
    assert all(co_old != spec.co)
    np.testing.assert_allclose(np.max(spec.co), 1.11636, rtol=1e-5)
    np.testing.assert_allclose(np.min(spec.co), 0.8658872, rtol=1e-5)

    #test reset
    spec.reset_continuum()
    np.testing.assert_allclose(spec.co, 1.)

    # Test generation of normalized spec
    norm_spec = spec.normalized_spec()
    assert isinstance(norm_spec, XSpectrum1D)
    assert norm_spec.normed is False

    # test normalize/unnormalize
    flux_old = spec.flux
    spec.unnormalize()
    assert spec.normed is False
    np.testing.assert_allclose(spec.flux,flux_old)


def test_assignment(spec):
    temp = np.arange(1, spec.npix + 1)
    spec.wavelength = temp * u.m
    assert spec.wavelength[0] == temp[0] * u.m
    unit = u.erg / u.s / u.cm**2 / u.AA
    spec.flux = temp * unit
    assert spec.flux[0] == temp[0] * unit
    spec.sig = temp
    assert spec.sig[0] == temp[0] * unit
    spec.co = temp
    assert spec.co[0] == temp[0] * unit


def test_wvmnx():
    npix = 1000
    # Without sig
    spec = XSpectrum1D.from_tuple((np.linspace(5000.,6000,npix), np.ones(npix)))
    assert spec.wvmin.value == 5000.
    assert spec.wvmax.value == 6000.
    # With sig
    spec = XSpectrum1D.from_tuple((np.linspace(5000.,6000,npix), np.ones(npix),
                                   np.ones(npix)*0.1))
    assert spec.wvmin.value == 5000.
    assert spec.wvmax.value == 6000.
