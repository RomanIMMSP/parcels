import numpy as np
from abc import ABC
from abc import abstractmethod
from datetime import datetime
from datetime import timedelta as delta
from os import path
import time as time_module
import cftime
import sys  # noqa: F401

import progressbar

from parcels.tools.statuscodes import StateCode
from parcels.tools.global_statics import get_package_dir
from parcels.compilation.codecompiler import GNUCompiler
from parcels.field import NestedField
from parcels.field import SummedField
from parcels.application_kernels.advection import AdvectionRK4
from parcels.kernel.basekernel import BaseKernel  # as Kernel
from parcels.collection.collections import ParticleCollection
from parcels.tools.loggers import logger
from parcels.interaction.baseinteractionkernel import BaseInteractionKernel


class NDCluster(ABC):
    """Interface."""


class BaseParticleSet(NDCluster):
    """Base ParticleSet."""
    _collection = None
    _fieldset = None
    _kernel = None
    # kernel = None
    _kclass = None
    interaction_kernel = None
    time_origin = None
    repeatdt = None
    repeatpclass = None
    repeatlon = None
    repeatlat = None
    repeatdepth = None
    repeatkwargs = None
    repeat_starttime = None

    def __init__(self, fieldset=None, pclass=None, lon=None, lat=None,
                 depth=None, time=None, repeatdt=None, lonlatdepth_dtype=None, pid_orig=None, **kwargs):
        self._collection = None
        self.repeat_starttime = None
        self.repeatlon = None
        self.repeatlat = None
        self.repeatdepth = None
        self.repeatpclass = None
        self.repeatkwargs = None
        self._kernel = None
        # self.kernel = None  # should be removed, because - for write protection - 'self.kernel' shall be a non-writeable property of variable self._kernel
        self.interaction_kernel = None
        self._fieldset = None
        # self.fieldset = None  # should be removed, because - for write protection - 'self.fieldset' shall be a non-writeable property of variable self._fieldset
        self.time_origin = None

    def __del__(self):
        if self._collection is not None and isinstance(self._collection, ParticleCollection):
            # logger.info("BaseParticleSet.del() - deleting collection of type '{}'.".format(type(self._collection)))
            del self._collection
        else:
            # logger.info("BaseParticleSet.del() - no collection available.")
            pass
        self._collection = None
        # super(BaseParticleSet, self).__del__()

    def clear(self):
        try:
            self._collection.clear()
        except (AttributeError):
            pass

    def iterator(self):
        return self._collection.iterator()

    def __iter__(self):
        """Allows for more intuitive iteration over a particleset, while
        in reality iterating over the particles in the collection.
        """
        return self.iterator()

    def __getattr__(self, name):
        """
        Access a single property of all particles.

        :param name: name of the property
        """
        for v in self._collection.ptype.variables:
            if v.name == name:
                return getattr(self._collection, name)
        if name in self.__dict__ and name[0] != '_':
            return self.__dict__[name]
        else:
            return False

    @staticmethod
    def lonlatdepth_dtype_from_field_interp_method(field):
        if type(field) in [SummedField, NestedField]:
            for f in field:
                if f.interp_method == 'cgrid_velocity':
                    return np.float64
        else:
            if field.interp_method == 'cgrid_velocity':
                return np.float64
        return np.float32

    @property
    def collection(self):
        return self._collection

    @property
    def fieldset(self):
        return self._fieldset

    # ==== no implementation of setter, as fieldset needs to be initialized on construction ==== #

    @property
    def kernel(self):
        return self._kernel

    # ==== no implementation of setter, as kernel needs to be initialized on construction ==== #

    @property
    def kernelclass(self):
        return (self._kclass if self._kclass is not None else BaseKernel)

    @kernelclass.setter
    def kernelclass(self, value):
        self._kclass = value

    @property
    def lonlatdepth_dtype(self):
        return self._collection.lonlatdepth_dtype

    @property
    def ptype(self):
        return self._collection.ptype

    @property
    def pclass(self):
        return self._collection.pclass

    @abstractmethod
    def cstruct(self):
        """
        'cstruct' returns the ctypes mapping of the combined collections cstruct and the fieldset cstruct.
        This depends on the specific structure in question.
        """
        pass

    def _create_progressbar_(self, starttime, endtime):
        pbar = None
        try:
            pbar = progressbar.ProgressBar(max_value=abs(endtime - starttime)).start()
        except:  # for old versions of progressbar
            try:
                pbar = progressbar.ProgressBar(maxvalue=abs(endtime - starttime)).start()
            except:  # for even older OR newer versions
                pbar = progressbar.ProgressBar(maxval=abs(endtime - starttime)).start()
        return pbar

    @classmethod
    def from_list(cls, fieldset, pclass, lon, lat, depth=None, time=None, repeatdt=None, lonlatdepth_dtype=None, **kwargs):
        """Initialise the ParticleSet from lists of lon and lat

        :param fieldset: :mod:`parcels.fieldset.FieldSet` object from which to sample velocity
        :param pclass: mod:`parcels.particle.JITParticle` or :mod:`parcels.particle.ScipyParticle`
                 object that defines custom particle
        :param lon: List of initial longitude values for particles
        :param lat: List of initial latitude values for particles
        :param depth: Optional list of initial depth values for particles. Default is 0m
        :param time: Optional list of start time values for particles. Default is fieldset.U.time[0]
        :param repeatdt: Optional interval (in seconds) on which to repeat the release of the ParticleSet
        :param lonlatdepth_dtype: Floating precision for lon, lat, depth particle coordinates.
               It is either np.float32 or np.float64. Default is np.float32 if fieldset.U.interp_method is 'linear'
               and np.float64 if the interpolation method is 'cgrid_velocity'
        Other Variables can be initialised using further arguments (e.g. v=... for a Variable named 'v')
       """
        return cls(fieldset=fieldset, pclass=pclass, lon=lon, lat=lat, depth=depth, time=time, repeatdt=repeatdt, lonlatdepth_dtype=lonlatdepth_dtype, **kwargs)

    @classmethod
    def from_line(cls, fieldset, pclass, start, finish, size, depth=None, time=None, repeatdt=None, lonlatdepth_dtype=None, **kwargs):
        """Initialise the ParticleSet from start/finish coordinates with equidistant spacing
        Note that this method uses simple numpy.linspace calls and does not take into account
        great circles, so may not be a exact on a globe

        :param fieldset: :mod:`parcels.fieldset.FieldSet` object from which to sample velocity
        :param pclass: mod:`parcels.particle.JITParticle` or :mod:`parcels.particle.ScipyParticle`
                 object that defines custom particle
        :param start: Starting point for initialisation of particles on a straight line.
        :param finish: End point for initialisation of particles on a straight line.
        :param size: Initial size of particle set
        :param depth: Optional list of initial depth values for particles. Default is 0m
        :param time: Optional start time value for particles. Default is fieldset.U.time[0]
        :param repeatdt: Optional interval (in seconds) on which to repeat the release of the ParticleSet
        :param lonlatdepth_dtype: Floating precision for lon, lat, depth particle coordinates.
               It is either np.float32 or np.float64. Default is np.float32 if fieldset.U.interp_method is 'linear'
               and np.float64 if the interpolation method is 'cgrid_velocity'
        """

        lon = np.linspace(start[0], finish[0], size)
        lat = np.linspace(start[1], finish[1], size)
        if type(depth) in [int, float]:
            depth = [depth] * size
        return cls(fieldset=fieldset, pclass=pclass, lon=lon, lat=lat, depth=depth, time=time, repeatdt=repeatdt, lonlatdepth_dtype=lonlatdepth_dtype)

    def merge(self, other):
        """
        This function merges another particle set into this one.
        """
        self._collection.merge(other.collection)

    def split(self, keys):
        """
        splits a particle set according to indices or ids, returning the resulting new subset that is not part anymore of this particle set
        :param key: indices (int; np.int32; np.uin32), or IDs (np.int64, np.uint64)
        :return: ParticleSet
        """
        return self.split_same(keys)

    def split_same(self, subset):
        """
        This function splits this collection into two disect equi-structured collections. The reason for it can, for
        example, be that the set exceeds a pre-defined maximum number of elements, which for performance reasons
        mandates a split.

        The function shall return the newly created or extended Particle collection, i.e. either the collection that
        results from a collection split or this very collection, containing the newly-split particles.
        """
        subset_are_indices = False
        subset_are_ids = False
        assert subset is not None
        assert (subset.shape[0] if isinstance(subset, np.ndarray) else len(subset)) > 0
        if isinstance(subset, np.ndarray) and (subset.dtype == np.int32 or subset.dtype == np.uint32):
            subset_are_indices = True
        elif isinstance(subset, list) and len(subset) > 0 and (isinstance(subset[0], int) or isinstance(subset[0], np.int32) or isinstance(subset[0], np.uint32)):
            subset_are_indices = True
        elif isinstance(subset, np.ndarray) and (subset.dtype == np.int64 or subset.dtype == np.uint64):
            subset_are_ids = True
        elif isinstance(subset, list) and len(subset) > 0 and (isinstance(subset[0], int) or isinstance(subset[0], np.int64) or isinstance(subset[0], np.uint64)):
            subset_are_ids = True
        assert subset_are_ids or subset_are_indices
        if subset_are_indices:
            return self.split_by_index(subset)
        elif subset_are_ids:
            return self.split_by_id(subset)
        return None

    @abstractmethod
    def split_by_index(self, indices):
        """
        This function splits this collection into two disect equi-structured collections using the indices as subset.
        The reason for it can, for example, be that the set exceeds a pre-defined maximum number of elements, which for
        performance reasons mandates a split.

        The function shall return the newly created or extended Particle collection, i.e. either the collection that
        results from a collection split or this very collection, containing the newly-split particles.
        """
        subset_are_indices = False
        assert indices is not None
        assert (indices.shape[0] if isinstance(indices, np.ndarray) else len(indices)) > 0
        if isinstance(indices, np.ndarray) and (indices.dtype == np.int32 or indices.dtype == np.uint32):
            subset_are_indices = True
        elif isinstance(indices, list) and len(indices) > 0 and (isinstance(indices[0], int) or isinstance(indices[0], np.int32) or isinstance(indices[0], np.uint32)):
            subset_are_indices = True
        assert subset_are_indices
        return None

    @abstractmethod
    def split_by_id(self, ids):
        """
        This function splits this collection into two disect equi-structured collections using the ID as subset.
        The reason for it can, for example, be that the set exceeds a pre-defined maximum number of elements, which for
        performance reasons mandates a split.

        The function shall return the newly created or extended Particle collection, i.e. either the collection that
        results from a collection split or this very collection, containing the newly-split particles.
        """
        subset_are_ids = False
        assert ids is not None
        assert (ids.shape[0] if isinstance(ids, np.ndarray) else len(ids)) > 0
        if isinstance(ids, np.ndarray) and (ids.dtype == np.int64 or ids.dtype == np.uint64):
            subset_are_ids = True
        elif isinstance(ids, list) and len(ids) > 0 and (isinstance(ids[0], int) or isinstance(ids[0], np.int64) or isinstance(ids[0], np.uint64)):
            subset_are_ids = True
        assert subset_are_ids
        return None

    @property
    def size(self):
        # ==== to change at some point - len and size are different things ==== #
        return len(self._collection)

    def __len__(self):
        """
        :returns number of elements in the particle set
        """
        return len(self._collection)

    def __sizeof__(self):
        """
        This function returns the size in actual bytes required in memory to hold the particle set. Ideally and simply,
        the size is computed as follows:

        sizeof(self) = len(self) * sizeof(pclass)
        :returns size of this collection in bytes; initiated by calling sys.getsizeof(object)
        """
        sz = sys.getsizeof(self._collection)
        sz += sys.getsizeof(self._kernel)
        sz += sys.getsizeof(self.repeatdt) if self.repeatdt is not None else 0
        sz += sys.getsizeof(self.repeatlon) if self.repeatlon is not None else 0
        sz += sys.getsizeof(self.repeatlat) if self.repeatlat is not None else 0
        sz += sys.getsizeof(self.repeatdepth) if self.repeatdepth is not None else 0
        sz += sys.getsizeof(self.repeatkwargs) if self.repeatkwargs is not None else 0
        return sz

    @classmethod
    @abstractmethod
    def monte_carlo_sample(cls, start_field, size, mode='monte_carlo'):
        """
        Converts a starting field into a monte-carlo sample of lons and lats.

        :param start_field: :mod:`parcels.fieldset.Field` object for initialising particles stochastically (horizontally)  according to the presented density field.

        returns list(lon), list(lat)
        """
        pass

    @classmethod
    def from_field(cls, fieldset, pclass, start_field, size, mode='monte_carlo', depth=None, time=None, repeatdt=None, lonlatdepth_dtype=None, **kwargs):
        """Initialise the ParticleSet randomly drawn according to distribution from a field

        :param fieldset: :mod:`parcels.fieldset.FieldSet` object from which to sample velocity
        :param pclass: mod:`parcels.particle.JITParticle` or :mod:`parcels.particle.ScipyParticle`
                 object that defines custom particle
        :param start_field: Field for initialising particles stochastically (horizontally)  according to the presented density field.
        :param size: Initial size of particle set
        :param mode: Type of random sampling. Currently only 'monte_carlo' is implemented
        :param depth: Optional list of initial depth values for particles. Default is 0m
        :param time: Optional start time value for particles. Default is fieldset.U.time[0]
        :param repeatdt: Optional interval (in seconds) on which to repeat the release of the ParticleSet
        :param lonlatdepth_dtype: Floating precision for lon, lat, depth particle coordinates.
               It is either np.float32 or np.float64. Default is np.float32 if fieldset.U.interp_method is 'linear'
               and np.float64 if the interpolation method is 'cgrid_velocity'
        """

        lon, lat = cls.monte_carlo_sample(start_field, size, mode)

        return cls(fieldset=fieldset, pclass=pclass, lon=lon, lat=lat, depth=depth, time=time, lonlatdepth_dtype=lonlatdepth_dtype, repeatdt=repeatdt)

    @classmethod
    @abstractmethod
    def from_particlefile(cls, fieldset, pclass, filename, restart=True, restarttime=None, repeatdt=None, lonlatdepth_dtype=None, **kwargs):
        """Initialise the ParticleSet from a netcdf ParticleFile.
        This creates a new ParticleSet based on locations of all particles written
        in a netcdf ParticleFile at a certain time. Particle IDs are preserved if restart=True

        :param fieldset: :mod:`parcels.fieldset.FieldSet` object from which to sample velocity
        :param pclass: mod:`parcels.particle.JITParticle` or :mod:`parcels.particle.ScipyParticle`
                 object that defines custom particle
        :param filename: Name of the particlefile from which to read initial conditions
        :param restart: Boolean to signal if pset is used for a restart (default is True).
               In that case, Particle IDs are preserved.
        :param restarttime: time at which the Particles will be restarted. Default is the last time written.
               Alternatively, restarttime could be a time value (including np.datetime64) or
               a callable function such as np.nanmin. The last is useful when running with dt < 0.
        :param repeatdt: Optional interval (in seconds) on which to repeat the release of the ParticleSet
        :param lonlatdepth_dtype: Floating precision for lon, lat, depth particle coordinates.
               It is either np.float32 or np.float64. Default is np.float32 if fieldset.U.interp_method is 'linear'
               and np.float64 if the interpolation method is 'cgrid_velocity'
        """
        pass

    def density(self, field_name=None, particle_val=None, relative=False, area_scale=False):
        """Method to calculate the density of particles in a ParticleSet from their locations,
        through a 2D histogram.

        :param field: Optional :mod:`parcels.field.Field` object to calculate the histogram
                      on. Default is `fieldset.U`
        :param particle_val: Optional numpy-array of values to weigh each particle with,
                             or string name of particle variable to use weigh particles with.
                             Default is None, resulting in a value of 1 for each particle
        :param relative: Boolean to control whether the density is scaled by the total
                         weight of all particles. Default is False
        :param area_scale: Boolean to control whether the density is scaled by the area
                           (in m^2) of each grid cell. Default is False
        """
        pass

    @abstractmethod
    def Kernel(self, pyfunc, c_include="", delete_cfiles=True):
        """Wrapper method to convert a `pyfunc` into a :class:`parcels.kernel.Kernel` object
        based on `fieldset` and `ptype` of the ParticleSet
        :param delete_cfiles: Boolean whether to delete the C-files after compilation in JIT mode (default is True)
        """
        pass

    def InteractionKernel(self, pyfunc_inter):
        raise NotImplementedError("Particle set type is not compatible with interaction.")

    @abstractmethod
    def ParticleFile(self, *args, **kwargs):
        """Wrapper method to initialise a :class:`parcels.particlefile.ParticleFile`
        object from the ParticleSet"""
        pass

    @abstractmethod
    def _set_particle_vector(self, name, value):
        """Set attributes of all particles to new values.

        This is a fallback implementation, it might be slow.

        :param name: Name of the attribute (str).
        :param value: New value to set the attribute of the particles to.
        """
        for p in self:
            setattr(p, name, value)

    @property
    @abstractmethod
    def error_particles(self):
        """Get an iterator over all particles that are in an error state.

        This is a fallback implementation, it might be slow.

        :return: Collection iterator over error particles.
        """
        error_indices = [
            i for i, p in enumerate(self)
            if p.state not in [StateCode.Success, StateCode.Evaluate]]
        return self.collection.get_multi_by_indices(indices=error_indices)

    @property
    def num_error_particles(self):
        """Get the number of particles that are in an error state."""
        return len([True if p.state not in [StateCode.Success, StateCode.Evaluate] else None for p in self])

    @abstractmethod
    def _impute_release_times(self, default):
        """Set attribute 'time' to default if encountering NaN values.

        This is a fallback implementation, it might be slow.

        :param default: Default release time.
        :return: Minimum and maximum release times.
        """
        max_rt = None
        min_rt = None
        for p in self:
            if np.isnan(p.time):
                p.time = default
            if max_rt is None or max_rt < p.time:
                max_rt = p.time
            if min_rt is None or min_rt > p.time:
                min_rt = p.time
        return min_rt, max_rt

    def execute(self, pyfunc=AdvectionRK4, pyfunc_inter=None, endtime=None, runtime=None, dt=1.,
                moviedt=None, recovery=None, output_file=None, movie_background_field=None,
                verbose_progress=None, postIterationCallbacks=None, callbackdt=None):
        """Execute a given kernel function over the particle set for
        multiple timesteps. Optionally also provide sub-timestepping
        for particle output.

        :param pyfunc: Kernel function to execute. This can be the name of a
                       defined Python function or a :class:`parcels.kernel.Kernel` object.
                       Kernels can be concatenated using the + operator
        :param endtime: End time for the timestepping loop.
                        It is either a datetime object or a positive double.
        :param runtime: Length of the timestepping loop. Use instead of endtime.
                        It is either a timedelta object or a positive double.
        :param dt: Timestep interval to be passed to the kernel.
                   It is either a timedelta object or a double.
                   Use a negative value for a backward-in-time simulation.
        :param moviedt:  Interval for inner sub-timestepping (leap), which dictates
                         the update frequency of animation.
                         It is either a timedelta object or a positive double.
                         None value means no animation.
        :param output_file: :mod:`parcels.particlefile.ParticleFile` object for particle output
        :param recovery: Dictionary with additional `:mod:parcels.tools.error`
                         recovery kernels to allow custom recovery behaviour in case of
                         kernel errors.
        :param movie_background_field: field plotted as background in the movie if moviedt is set.
                                       'vector' shows the velocity as a vector field.
        :param verbose_progress: Boolean for providing a progress bar for the kernel execution loop.
        :param postIterationCallbacks: (Optional) Array of functions that are to be called after each iteration (post-process, non-Kernel)
        :param callbackdt: (Optional, in conjecture with 'postIterationCallbacks) timestep inverval to (latestly) interrupt the running kernel and invoke post-iteration callbacks from 'postIterationCallbacks'
        """
        # check if pyfunc has changed since last compile. If so, recompile.
        # COMMENT #1034: this still needs to check that the ParticleClass name also didn't change!
        if self.kernel is None or (self.kernel.pyfunc is not pyfunc and self.kernel is not pyfunc):
            # Generate and store Kernel
            if isinstance(pyfunc, BaseKernel):
                assert isinstance(pyfunc, self.kernelclass), "Trying to mix kernels of different particle set structures - action prohibited. Please construct the kernel for this specific particle set '{}'.".format(type(self).__name__)
                if pyfunc.ptype.name == self.collection.ptype.name:
                    self._kernel = pyfunc
                elif pyfunc.pyfunc is not None:
                    self._kernel = self.Kernel(pyfunc.pyfunc)
                else:
                    raise RuntimeError("Cannot reuse concatenated kernels that were compiled for different particle types. Please rebuild the 'pyfunc' or 'kernel' given to the execute function.")
            else:
                self._kernel = self.Kernel(pyfunc)
            # Prepare JIT kernel execution
            if self.collection.ptype.uses_jit:
                # logger.info("Compiling particle class {} with kernel function {} into KernelName {}".format(self.collection.pclass, self.kernel.funcname, self.kernel.name))
                self.kernel.remove_lib()
                cppargs = ['-DDOUBLE_COORD_VARIABLES'] if self.collection.lonlatdepth_dtype else None
                self.kernel.compile(compiler=GNUCompiler(cppargs=cppargs, incdirs=[path.join(get_package_dir(), 'include'), "."]))
                self.kernel.load_lib()

        # Set up the interaction kernel(s) if not set and given.
        if self.interaction_kernel is None and pyfunc_inter is not None:
            if isinstance(pyfunc_inter, BaseInteractionKernel):
                self.interaction_kernel = pyfunc_inter
            else:
                self.interaction_kernel = self.InteractionKernel(pyfunc_inter)

        # Convert all time variables to seconds
        if isinstance(endtime, delta):
            raise RuntimeError('endtime must be either a datetime or a double')
        if isinstance(endtime, datetime):
            endtime = np.datetime64(endtime)
        elif isinstance(endtime, cftime.datetime):
            endtime = self.time_origin.reltime(endtime)
        if isinstance(endtime, np.datetime64):
            if self.time_origin.calendar is None:
                raise NotImplementedError('If fieldset.time_origin is not a date, execution endtime must be a double')
            endtime = self.time_origin.reltime(endtime)
        if isinstance(runtime, delta):
            runtime = runtime.total_seconds()
        if isinstance(dt, delta):
            dt = dt.total_seconds()
        outputdt = output_file.outputdt if output_file else np.infty
        if isinstance(outputdt, delta):
            outputdt = outputdt.total_seconds()
        if isinstance(moviedt, delta):
            moviedt = moviedt.total_seconds()
        if isinstance(callbackdt, delta):
            callbackdt = callbackdt.total_seconds()

        assert runtime is None or runtime >= 0, 'runtime must be positive'
        assert outputdt is None or outputdt >= 0, 'outputdt must be positive'
        assert moviedt is None or moviedt >= 0, 'moviedt must be positive'

        if runtime is not None and endtime is not None:
            raise RuntimeError('Only one of (endtime, runtime) can be specified')

        mintime, maxtime = self.fieldset.gridset.dimrange('time_full') if self.fieldset is not None else (0, 1)

        default_release_time = mintime if dt >= 0 else maxtime
        min_rt, max_rt = self._impute_release_times(default_release_time)

        # Derive _starttime and endtime from arguments or fieldset defaults
        _starttime = min_rt if dt >= 0 else max_rt
        if self.repeatdt is not None and self.repeat_starttime is None:
            self.repeat_starttime = _starttime
        if runtime is not None:
            endtime = _starttime + runtime * np.sign(dt)
        elif endtime is None:
            mintime, maxtime = self.fieldset.gridset.dimrange('time_full')
            endtime = maxtime if dt >= 0 else mintime

        execute_once = False
        # if abs(endtime - _starttime) < 1e-5 or np.isclose(dt, 0) or (runtime is None or np.isclose(runtime, 0)):
        if abs(endtime-_starttime) < 1e-5 or dt == 0 or runtime == 0:
            dt = 0
            runtime = 0
            endtime = _starttime
            logger.warning_once("dt or runtime are zero, or endtime is equal to Particle.time. "
                                "The kernels will be executed once, without incrementing time")
            execute_once = True

        self._set_particle_vector('dt', dt)

        # First write output_file, because particles could have been added
        if output_file:
            output_file.write(self, _starttime)
        if moviedt:
            self.show(field=movie_background_field, show_time=_starttime, animation=True)

        if moviedt is None:
            moviedt = np.infty
        if callbackdt is None:
            interupt_dts = [np.infty, moviedt, outputdt]
            if self.repeatdt is not None:
                interupt_dts.append(self.repeatdt)
            callbackdt = np.min(np.array(interupt_dts))
        time = _starttime
        if self.repeatdt:
            next_prelease = self.repeat_starttime + (abs(time - self.repeat_starttime) // self.repeatdt + 1) * self.repeatdt * np.sign(dt)
        else:
            next_prelease = np.infty if dt > 0 else - np.infty
        next_output = time + outputdt if dt > 0 else time - outputdt
        next_movie = time + moviedt if dt > 0 else time - moviedt
        next_callback = time + callbackdt if dt > 0 else time - callbackdt
        # logger.info("compute time-chunk input for time = {} and dt = {} ...".format(time, dt))
        next_input = self.fieldset.computeTimeChunk(time, np.sign(dt)) if self.fieldset is not None else np.inf
        # logger.info("Time-chunk input for time = {} and dt = {} computed.".format(time, dt))

        tol = 1e-12

        pbar = None
        walltime_start = None
        if verbose_progress is None:
            walltime_start = time_module.time()
        if verbose_progress:
            pbar = self._create_progressbar_(_starttime, endtime)

        while (time < endtime and dt > 0) or (time > endtime and dt < 0) or dt == 0:
            if verbose_progress is None and time_module.time() - walltime_start > 10:
                # Showing progressbar if runtime > 10 seconds
                if output_file:
                    logger.info('Temporary output files are stored in %s.' % output_file.tempwritedir_base)
                    logger.info('You can use "parcels_convert_npydir_to_netcdf %s" to convert these '
                                'to a NetCDF file during the run.' % output_file.tempwritedir_base)
                pbar = self._create_progressbar_(_starttime, endtime)
                verbose_progress = True

            if dt > 0:
                next_time = min(next_prelease, next_input, next_output, next_movie, next_callback, endtime)
            else:
                next_time = max(next_prelease, next_input, next_output, next_movie, next_callback, endtime)
            # logger.info("Executing next timestep is time = {} ...".format(next_time))

            # If we don't perform interaction, only execute the normal kernel efficiently.
            if self.interaction_kernel is None:
                self.kernel.execute(self, endtime=next_time, dt=dt, recovery=recovery, output_file=output_file,
                                    execute_once=execute_once)
            # Interaction: interleave the interaction and non-interaction kernel for each time step.
            # E.g. Inter -> Normal -> Inter -> Normal if endtime-time == 2*dt
            else:
                cur_time = time
                while (cur_time < next_time and dt > 0) or (cur_time > next_time and dt < 0) or dt == 0:
                    if dt > 0:
                        cur_end_time = min(cur_time+dt, next_time)
                    else:
                        cur_end_time = max(cur_time+dt, next_time)
                    self.interaction_kernel.execute(
                        self, endtime=cur_end_time, dt=dt, recovery=recovery,
                        output_file=output_file, execute_once=execute_once)
                    self.kernel.execute(
                        self, endtime=cur_end_time, dt=dt, recovery=recovery,
                        output_file=output_file, execute_once=execute_once)
                    cur_time += dt
                    if dt == 0:
                        break
            # End of interaction specific code
            time = next_time
            # logger.info("Kernel executed - time: {}; repeatdt: {}; repeat_starttime: {}; next_prelease: {}; repeatlon: {}".format(time, self.repeatdt, self.repeat_starttime, next_prelease, self.repeatlon))
            if abs(time-next_prelease) < tol:
                pset_new = self.__class__(
                    fieldset=self.fieldset, time=time, lon=self.repeatlon,
                    lat=self.repeatlat, depth=self.repeatdepth,
                    pclass=self.repeatpclass,
                    lonlatdepth_dtype=self.collection.lonlatdepth_dtype,
                    partitions=False, pid_orig=self.repeatpid, **self.repeatkwargs)
                for p in pset_new:
                    p.dt = dt
                self.add(pset_new)
                next_prelease += self.repeatdt * np.sign(dt)
            if abs(time - next_output) < tol or dt == 0:
                for fld in self.fieldset.get_fields():
                    if hasattr(fld, 'to_write') and fld.to_write:
                        if fld.grid.tdim > 1:
                            raise RuntimeError('Field writing during execution only works for Fields with one snapshot in time')
                        fldfilename = str(output_file.name).replace('.nc', '_%.4d' % fld.to_write)  # what does this do ? the variable is boolean, then it's increased - what-the-frog ...
                        fld.write(fldfilename)
                        fld.to_write += 1
            if abs(time - next_output) < tol:
                if output_file:
                    output_file.write(self, time)
                next_output += outputdt * np.sign(dt)
            if abs(time-next_movie) < tol:
                self.show(field=movie_background_field, show_time=time, animation=True)
                next_movie += moviedt * np.sign(dt)
            # ==== insert post-process here to also allow for memory clean-up via external func ==== #
            if abs(time-next_callback) < tol:
                if postIterationCallbacks is not None:
                    for extFunc in postIterationCallbacks:
                        extFunc()
                next_callback += callbackdt * np.sign(dt)
            if time != endtime:
                next_input = self.fieldset.computeTimeChunk(time, dt)
            if dt == 0:
                break
            if verbose_progress:
                pbar.update(abs(time - _starttime))

        if output_file:
            output_file.write(self, time)
        if verbose_progress:
            pbar.finish()

    def show(self, with_particles=True, show_time=None, field=None, domain=None, projection=None,
             land=True, vmin=None, vmax=None, savefile=None, animation=False, **kwargs):
        """Method to 'show' a Parcels ParticleSet

        :param with_particles: Boolean whether to show particles
        :param show_time: Time at which to show the ParticleSet
        :param field: Field to plot under particles (either None, a Field object, or 'vector')
        :param domain: dictionary (with keys 'N', 'S', 'E', 'W') defining domain to show
        :param projection: type of cartopy projection to use (default PlateCarree)
        :param land: Boolean whether to show land. This is ignored for flat meshes
        :param vmin: minimum colour scale (only in single-plot mode)
        :param vmax: maximum colour scale (only in single-plot mode)
        :param savefile: Name of a file to save the plot to
        :param animation: Boolean whether result is a single plot, or an animation
        """
        from parcels.plotting import plotparticles
        plotparticles(particles=self, with_particles=with_particles, show_time=show_time, field=field, domain=domain,
                      projection=projection, land=land, vmin=vmin, vmax=vmax, savefile=savefile, animation=animation, **kwargs)
