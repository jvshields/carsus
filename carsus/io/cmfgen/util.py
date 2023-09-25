import gzip
import itertools

import astropy.constants as const
import astropy.units as u
import numpy as np
from scipy.interpolate import interp1d

from enum import IntEnum, unique

RYD_TO_EV = u.rydberg.to('eV')
H_IN_EV_SECONDS = const.h.to('eV s').value
HC_IN_EV_ANGSTROM = (const.h * const.c).to('eV angstrom').value
CMFGEN_ATOM_DICT = {
    'H': 'HYD', 'He': 'HE', 'C': 'CARB', 'N': 'NIT',
    'O': 'OXY', 'F': 'FLU', 'Ne': 'NEON', 'Na': 'NA',
    'Mg': 'MG', 'Al': 'AL', 'Si': 'SIL', 'P': 'PHOS',
    'S': 'SUL', 'Cl': 'CHL', 'Ar': 'ARG', 'K': 'POT',
    'Ca': 'CA', 'Sc': 'SCAN', 'Ti': 'TIT', 'V': 'VAN',
    'Cr': 'CHRO', 'Mn': 'MAN', 'Fe': 'FE', 'Co': 'COB',
    'Ni': 'NICK'
}


@unique
class CrossSectionType(IntEnum):
    CONSTANT_ZERO = 0
    SEATON_FITS = 1
    HYDROGENIC_SPLIT_L = 2
    HYDROGENIC_PURE_N_LEVEL = 3
    LEIBOWITZ_CIV_FITS = 4
    OPACITY_PROJECT_FITS = 5
    HUMMER_HEI_FITS = 6
    SEATON_FITS_OFFSET = 7
    HYDROGENIC_SPLIT_L_OFFSET = 8
    VERNER_YAKOLEV_GS_FITS = 9
    OPACITY_PROJECT_SC = 20
    OPACITY_PROJECT_SC_SM = 21
    POINTS_TABLE = 22


def open_cmfgen_file(fname, encoding='ISO-8859-1'):
    return gzip.open(fname, 'rt') if fname.endswith('.gz') else open(fname, encoding=encoding) 


def to_float(string):
    """
    String to float, also deals with Fortran 'D' type.

    Parameters
    ----------
    string : str

    Returns
    -------
    float
    """

    typos = {'1-.00': '10.00',      # `MG/VIII/23oct02/phot_sm_3000`, line 23340
             '*********': 'NaN',}   # `SUL/V/08jul99/phot_op.big`, lines 9255-9257

    string = typos.get(string) if string in typos.keys() else string

    return float(string.replace('D', 'E'))


def find_row(fname, string1, string2=None, how='AND'):
    """
    Search for strings in plain text files and returns the matching\
    line (or row number).

    Parameters
    ----------
    fname : str
        Path to plain text file.
    string1 : str
        String to search.
    string2 : str
        Secondary string to search (default is None).
    how : {'OR', 'AND', 'AND NOT'}
        Search method: `string1` <method> `string2`
            (default is 'AND').

    Returns
    -------
    int, str
        Returns matching row number and line.
    """

    if string2 is None:
        string2 = ''

    with open_cmfgen_file(fname) as f:
        n = 0
        for line in f:

            n += 1
            if how == 'OR':
                if string1 in line or string2 in line:
                    break

            if how == 'AND':
                if string1 in line and string2 in line:
                    break

            if how == 'AND NOT':
                if string1 in line and string2 not in line:
                    break

        else:
            n, line = None, None

    return n, line


def parse_header(fname, start=0, stop=50):
    """
    Parse header information from CMFGEN files.

    Parameters
    ----------
    fname : str
        Path to plain text file.
    start : int
        First line to search in (default is 0).
    stop : int
        Last line to search in (default is 50).

    Returns
    -------
    dict
        Dictionary containing header information.
    """

    header = {}
    with open_cmfgen_file(fname) as f:
        for line in itertools.islice(f, start, stop):
            if '!' in line:
                value, key = line.split('!')
                
                if len(key) > 1:
                    key = key.split('(')[0]

                header[key.strip()] = value.strip()

    return header


def get_seaton_phixs_table(threshold_energy_ryd, sigma_t, beta, s, nu_0=None, n_points=1000):
    """
    References:

        Atomic data for opacity calculations. I. General description

        Seaton, M. J.

        Journal of Physics B: Atomic, Molecular, and Optical Physics,
        Volume 20, Issue 23, pp. 6363-6378 (1987).
    """
    energy_grid = np.linspace(0.0, 1.0, n_points, endpoint=False)
    phixs_table = np.empty((len(energy_grid), 2))

    for i, c in enumerate(energy_grid):
        energy_div_threshold = 1 + 20 * (c ** 2)  # Taken from `artisatomic`

        if nu_0 is None:
            threshold_div_energy = energy_div_threshold ** -1
            cross_section = sigma_t * (beta + (1 - beta) * threshold_div_energy) * (threshold_div_energy ** s)

        else:
            threshold_energy_ev = threshold_energy_ryd * RYD_TO_EV
            offset_threshold_div_energy = energy_div_threshold**-1 * (1 + (nu_0 * 1e15 * H_IN_EV_SECONDS) / threshold_energy_ev)

            if offset_threshold_div_energy < 1.0:
                cross_section = sigma_t * (beta + (1 - beta) * (offset_threshold_div_energy)) * \
                    (offset_threshold_div_energy ** s)

            else:
                cross_section = 0.0

        phixs_table[i] = energy_div_threshold * threshold_energy_ryd, cross_section

    return phixs_table


def get_hydrogenic_n_phixs_table(
    hyd_gaunt_energy_grid_ryd,
    hyd_gaunt_factor,
    threshold_energy_ryd,
    n,
    hyd_n_phixs_stop2start_energy_ratio,
    hyd_n_phixs_num_points,
):
    """
    Citation required.
    """
    hyd_gaunt_energy_grid_ryd_n = np.array(hyd_gaunt_energy_grid_ryd[n])
    log_gaunt_factor_n = np.log(hyd_gaunt_factor[n])
    energy_grid = np.geomspace(
        hyd_gaunt_energy_grid_ryd_n.min(),
        hyd_gaunt_energy_grid_ryd_n.min() * hyd_n_phixs_stop2start_energy_ratio,
        hyd_n_phixs_num_points,
    )
    phixs_table = np.empty((len(energy_grid), 2))
    scale_factor = 7.91 / threshold_energy_ryd / n
    log_hyd_gaunt_factor_interpolator = interp1d(
        np.log(hyd_gaunt_energy_grid_ryd_n),
        log_gaunt_factor_n,
        fill_value=(log_gaunt_factor_n[0], log_gaunt_factor_n[-1]),
        bounds_error=False,
    )

    for i, energy_ryd in enumerate(energy_grid):
        energy_div_threshold = energy_ryd / energy_grid[0]

        if energy_div_threshold > 0:
            hyd_gaunt_factor_n = np.exp(
                log_hyd_gaunt_factor_interpolator(np.log(energy_ryd))
            )
            cross_section = (
                scale_factor * hyd_gaunt_factor_n / (energy_div_threshold) ** 3
            )
        else:
            cross_section = 0.0

        phixs_table[i][0] = energy_div_threshold * threshold_energy_ryd
        phixs_table[i][1] = cross_section

    return phixs_table


def get_hydrogenic_nl_phixs_table(hyd_phixs_energy_grid_ryd, hyd_phixs, threshold_energy_ryd, n, l_start, l_end, nu_0=None):
    """
    Citation required.
    """

    assert l_start >= 0
    assert l_end <= n - 1

    energy_grid = hyd_phixs_energy_grid_ryd[(n, l_start)]
    phixs_table = np.empty((len(energy_grid), 2))

    threshold_energy_ev = threshold_energy_ryd * RYD_TO_EV
    scale_factor = 1 / threshold_energy_ryd / (n ** 2) / ((l_end - l_start + 1) * (l_end + l_start + 1))

    for i, energy_ryd in enumerate(energy_grid):
        energy_div_threshold = energy_ryd / energy_grid[0]
        if nu_0 is None:
            u = energy_div_threshold
        else:
            e_0 = (nu_0 * 1e15 * H_IN_EV_SECONDS)
            u = threshold_energy_ev * energy_div_threshold / (e_0 + threshold_energy_ev)
        if u > 0:
            cross_section = 0.0
            for l in range(l_start, l_end + 1):
                assert np.array_equal(hyd_phixs_energy_grid_ryd[(n, l)], energy_grid)
                cross_section += (2 * l + 1) * hyd_phixs[(n, l)][i]
            cross_section = cross_section * scale_factor
        else:
            cross_section = 0.0

        phixs_table[i][0] = energy_div_threshold * threshold_energy_ryd
        phixs_table[i][1] = cross_section

    return phixs_table


def get_opproject_phixs_table(threshold_energy_ryd, a, b, c, d, e, n_points=1000):
    """
    References:

        Atomic data for opacity calculations. IX. The lithium isoelectronic 
        sequence.

        Peach, G. ; Saraph, H. E. ; Seaton, M. J.

        Journal of Physics B: Atomic, Molecular, and Optical Physics, 
        Volume 21, Issue 22, pp. 3669-3683 (1988).
    """
    energy_grid = np.linspace(0.0, 1.0, n_points, endpoint=False)
    phixs_table = np.empty((len(energy_grid), 2))

    for i, cb in enumerate(energy_grid):

        energy_div_threshold = 1 + 20 * (cb ** 2)
        u = energy_div_threshold
        x = np.log10(min(u, e))

        cross_section = 10 ** (a + x * (b + x * (c + x * d)))
        if u > e:
            cross_section *= (e / u) ** 2

        phixs_table[i] = energy_div_threshold * threshold_energy_ryd, cross_section

    return phixs_table


def get_hummer_phixs_table(threshold_energy_ryd, a, b, c, d, e, f, g, h, n_points=1000):
    """
    References:

        A Fast and Accurate Method for Evaluating the Nonrelativistic Free-free
        Gaunt Factor for Hydrogenic Ions.
        
        Hummer, D. G.

        Astrophysical Journal v.327, p.477
    """

    # Only applies to `He`. The threshold cross sections seems ok, but 
    # energy dependence could be slightly wrong. What is the `h` parameter
    # that is not used?.
    
    energy_grid = np.linspace(0.0, 1.0, n_points, endpoint=False)
    phixs_table = np.empty((len(energy_grid), 2))

    for i, c_en in enumerate(energy_grid):
        energy_div_threshold = 1 + 20 * (c_en ** 2)

        x = np.log10(energy_div_threshold)
        if x < e:
            cross_section = 10 ** (((d * x + c) * x + b) * x + a)

        else:
            cross_section = 10 ** (f + g * x)

        phixs_table[i] = energy_div_threshold * threshold_energy_ryd, cross_section

    return phixs_table


def get_vy95_phixs_table(threshold_energy_ryd, fit_coeff_table, n_points=1000):
    """
    References:

        Analytic FITS for partial photoionization cross sections.

        Verner, D. A. ; Yakovlev, D. G.

        Astronomy and Astrophysics Suppl., Vol. 109, p.125-133 (1995)
    """
    energy_grid = np.linspace(0.0, 1.0, n_points, endpoint=False)
    phixs_table = np.empty((len(energy_grid), 2))

    for i, c in enumerate(energy_grid):

        energy_div_threshold = 1 + 20 * (c ** 2)
        cross_section = 0.0

        for index, row in fit_coeff_table.iterrows():
            y = energy_div_threshold * row.at['E'] / row.at['E_0']
            P = row.at['P']
            Q = 5.5 + row.at['l'] - 0.5 * row.at['P']
            y_a = row.at['y(a)']
            y_w = row.at['y(w)']
            cross_section += row.at['sigma_0'] * ((y - 1) ** 2 + y_w ** 2) * (y ** -Q) * ((1 + np.sqrt(y / y_a)) ** -P)

        phixs_table[i] = energy_div_threshold * threshold_energy_ryd, cross_section

    return phixs_table


def get_leibowitz_phixs_table(threshold_energy_ryd, a, b, c, d, e, f, n_points=1000):
    """
    References:

        Radiative Transition Probabilities and Recombination Coefficients 
        of the Ion C IV.

        Leibowitz, E. M.

        J. Quant. Spectrosc. Radiat. Transfer. Vol 12, pp. 299-306.
    """
    
    raise NotImplementedError


def get_null_phixs_table(threshold_energy_ryd):
    """
    Returns a photoionization table with zero cross sections.

    Parameters
    ----------
    threshold_energy_ryd : float
        Photoionization threshold energy in Rydberg.

    Returns
    -------
    numpy.ndarray
        Photoionization cross sections table with zero cross sections.
    """
    energy = np.array([1., 100.]) * threshold_energy_ryd
    phixs = np.zeros_like(energy)
    phixs_table = np.column_stack([energy, phixs])

    return phixs_table
