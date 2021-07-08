from parcels import FieldSet, ScipyParticle, JITParticle, Variable, AdvectionRK4
from parcels import ParticleSetSOA, ParticleFileSOA, KernelSOA  # noqa
from parcels import ParticleSetAOS, ParticleFileAOS, KernelAOS  # noqa
from parcels import ParticleSetNodes, ParticleFileNodes, KernelNodes  # noqa
from parcels import GenerateID_Service, SequentialIdGenerator, LibraryRegisterC  # noqa
import numpy as np
import pytest
from operator import attrgetter
# from parcels.tools import logger

pset_modes = ['soa', 'aos', 'nodes']
ptype = {'scipy': ScipyParticle, 'jit': JITParticle}
pset_type = {'soa': {'pset': ParticleSetSOA, 'pfile': ParticleFileSOA, 'kernel': KernelSOA},
             'aos': {'pset': ParticleSetAOS, 'pfile': ParticleFileAOS, 'kernel': KernelAOS},
             'nodes': {'pset': ParticleSetNodes, 'pfile': ParticleFileNodes, 'kernel': KernelNodes}}


def fieldset(xdim=100, ydim=100):
    data = {'U': np.zeros((ydim, xdim), dtype=np.float32),
            'V': np.zeros((ydim, xdim), dtype=np.float32)}
    dimensions = {'lon': np.linspace(0, 1, xdim, dtype=np.float32),
                  'lat': np.linspace(0, 1, ydim, dtype=np.float32)}
    return FieldSet.from_data(data, dimensions, mesh='flat')


@pytest.fixture(name="fieldset")
def fieldset_fixture(xdim=100, ydim=100):
    return fieldset(xdim=xdim, ydim=ydim)


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_variable_init(fieldset, pset_mode, mode, npart=10):
    """Test that checks correct initialisation of custom variables"""
    idgen = None
    c_lib_register = None

    class TestParticle(ptype[mode]):
        p_float = Variable('p_float', dtype=np.float32, initial=10.)
        p_double = Variable('p_double', dtype=np.float64, initial=11.)
        p_int = Variable('p_int', dtype=np.int32, initial=12.)

    pset = None
    if pset_mode != 'nodes':
        pset = pset_type[pset_mode]['pset'](fieldset, pclass=TestParticle,
                                            lon=np.linspace(0, 1, npart),
                                            lat=np.linspace(1, 0, npart))
    else:
        idgen = GenerateID_Service(SequentialIdGenerator)
        idgen.setDepthLimits(0., 1.0)
        idgen.setTimeLine(0.0, 1.0)
        c_lib_register = LibraryRegisterC()
        pset = pset_type[pset_mode]['pset'](idgen, fieldset, pclass=TestParticle,
                                            lon=np.linspace(0, 1, npart),
                                            lat=np.linspace(1, 0, npart),
                                            c_lib_register=c_lib_register)

    def addOne(particle, fieldset, time):
        particle.p_float += 1.
        particle.p_double += 1.
        particle.p_int += 1
    pset.execute(pset.Kernel(AdvectionRK4)+addOne, runtime=1., dt=1.)
    assert np.allclose([p.p_float for p in pset], 11., rtol=1e-12)
    assert np.allclose([p.p_double for p in pset], 12., rtol=1e-12)
    assert np.allclose([p.p_int for p in pset], 13, rtol=1e-12)

    # if pset_mode == 'nodes':
    #     node_c_interface = c_lib_register.get("node")
    #     num_nodes_registered = 0
    #     if node_c_interface is not None:
    #         num_nodes_registered = node_c_interface.register_count
    #     logger.info("# C-lib. registered nodes before PSet deletion: {}".format(num_nodes_registered))
    #     if num_nodes_registered > 0:
    #         i = 0
    #         for ndata in pset.collection.data:
    #             logger.info("Node {}: {}".format(i, ndata))
    del pset
    # if pset_mode == 'nodes':
    #     node_c_interface = c_lib_register.get("node")
    #     num_nodes_registered = 0
    #     if node_c_interface is not None:
    #         num_nodes_registered = node_c_interface.register_count
    #     logger.info("# C-lib. registered nodes after PSet deletion: {}".format(num_nodes_registered))

    if idgen is not None:
        idgen.close()
        del idgen
    if c_lib_register is not None:
        c_lib_register.clear()
        del c_lib_register


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['jit'])
@pytest.mark.parametrize('type', ['np.int8', 'mp.float', 'np.int16'])
def test_variable_unsupported_dtypes(fieldset, pset_mode, mode, type):
    """Test that checks errors thrown for unsupported dtypes in JIT mode"""
    idgen = None
    c_lib_register = None

    class TestParticle(ptype[mode]):
        p = Variable('p', dtype=type, initial=10.)
    error_thrown = False
    try:
        pset = None
        if pset_mode != 'nodes':
            pset_type[pset_mode]['pset'](fieldset, pclass=TestParticle, lon=[0], lat=[0])
        else:
            idgen = GenerateID_Service(SequentialIdGenerator)
            idgen.setDepthLimits(0., 1.0)
            idgen.setTimeLine(0.0, 1.0)
            c_lib_register = LibraryRegisterC()
            pset_type[pset_mode]['pset'](idgen, fieldset, pclass=TestParticle, lon=[0], lat=[0], c_lib_register=c_lib_register)
        del pset
    except (RuntimeError, TypeError):
        error_thrown = True
    assert error_thrown

    if idgen is not None:
        idgen.close()
        del idgen
    if c_lib_register is not None:
        c_lib_register.clear()
        del c_lib_register


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
def test_variable_special_names(fieldset, pset_mode, mode):
    """Test that checks errors thrown for special names"""
    idgen = None
    c_lib_register = None
    if pset_mode == 'nodes':
        idgen = GenerateID_Service(SequentialIdGenerator)
        idgen.setDepthLimits(0., 1.0)
        idgen.setTimeLine(0.0, 1.0)
        c_lib_register = LibraryRegisterC()
    for vars in ['z', 'lon']:
        class TestParticle(ptype[mode]):
            tmp = Variable(vars, dtype=np.float32, initial=10.)
        error_thrown = False
        try:
            pset = None
            if pset_mode != 'nodes':
                pset_type[pset_mode]['pset'](fieldset, pclass=TestParticle, lon=[0], lat=[0])
            else:
                pset_type[pset_mode]['pset'](idgen, fieldset, pclass=TestParticle, lon=[0], lat=[0], c_lib_register=c_lib_register)
        except AttributeError:
            error_thrown = True
        assert error_thrown
        del pset

    if idgen is not None:
        idgen.close()
        del idgen
    if c_lib_register is not None:
        c_lib_register.clear()
        del c_lib_register


@pytest.mark.parametrize('pset_mode', pset_modes)
@pytest.mark.parametrize('mode', ['scipy', 'jit'])
@pytest.mark.parametrize('coord_type', [np.float32, np.float64])
def test_variable_init_relative(fieldset, pset_mode, mode, coord_type, npart=10):
    """Test that checks relative initialisation of custom variables"""
    # lonlat_type = np.float64 if coord_type == 'double' else np.float32
    lonlat_type = coord_type

    class TestParticle(ptype[mode]):
        p_base = Variable('p_base', dtype=lonlat_type, initial=10.)
        p_relative = Variable('p_relative', dtype=lonlat_type,
                              initial=attrgetter('p_base'))
        p_lon = Variable('p_lon', dtype=lonlat_type,
                         initial=attrgetter('lon'))
        p_lat = Variable('p_lat', dtype=lonlat_type,
                         initial=attrgetter('lat'))

    lon = np.linspace(0, 1, npart, dtype=lonlat_type)
    lat = np.linspace(1, 0, npart, dtype=lonlat_type)
    idgen = None
    c_lib_register = None
    pset = None
    if pset_mode != 'nodes':
        pset = pset_type[pset_mode]['pset'](fieldset, pclass=TestParticle, lon=lon, lat=lat, lonlatdepth_dtype=coord_type)
    else:
        idgen = GenerateID_Service(SequentialIdGenerator)
        idgen.setDepthLimits(0., 1.0)
        idgen.setTimeLine(0.0, 1.0)
        c_lib_register = LibraryRegisterC()
        pset = pset_type[pset_mode]['pset'](idgen, fieldset, pclass=TestParticle, lon=lon, lat=lat, lonlatdepth_dtype=coord_type, c_lib_register=c_lib_register)

    # Adjust base variable to test for aliasing effects
    for p in pset:
        p.p_base += 3.
    assert np.allclose([p.p_base for p in pset], 13., rtol=1e-12)
    assert np.allclose([p.p_relative for p in pset], 10., rtol=1e-12)
    assert np.allclose([p.p_lon for p in pset], lon, rtol=1e-12)
    assert np.allclose([p.p_lat for p in pset], lat, rtol=1e-12)

    pset.clear()
    del pset

    if idgen is not None:
        idgen.close()
        del idgen
    if c_lib_register is not None:
        c_lib_register.clear()
        del c_lib_register
