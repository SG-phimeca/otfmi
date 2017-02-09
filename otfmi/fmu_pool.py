# -*- coding: utf-8 -*-
# Copyright 2016 EDF. This software was developed with the collaboration of
# Phimeca Engineering (Sylvain Girard, girard@phimeca.com).
"""Provide multiprocessing of fmu simulations with pyfmi.
This is a rewrite of the fmu_pool submodule from EstimationPy by
Marco Bonvini : https://github.com/lbl-srg/EstimationPy.
"""

#§
import multiprocessing

from multiprocessing import Process, Queue
from threading import Thread

import operator
import numpy as np

from .fmi import simulate, strip_simulation

import logger

#§
class FMUProcess(Process):
    """Process for running a single simulation of an FMU model.

    Parameters:
    ----------
    model : Pyfmi model object (pyfmi.fmi.FMUModelXXX).

    queue : multiprocesing.Queue, buffer where simulations results are
    temporarily stored.

    index : Integer, maintain sort order of the simulaltions.

    max_retry : Integer, maximun number of retries when simulation crash.
    Retry 10 times by default.

    initialization_script : String, path to the script file.

    # name_output : Sequence of strings, names of the output variables.

    Additional keyword arguments are passed on to 'otfmi.simulate'.
    See 'otfmi.simulate' and 'otfmi.parse_kwargs_simulate'.

    """

    def __init__(self, model, queue, index, max_retry=10,
                 initialization_script=None, **kwargs):
        super(FMUProcess, self).__init__()
        self.model = model
        self.max_retry = max_retry
        self.queue = queue
        self.index = index
        self.name_output = kwargs.pop("name_output",
                                      kwargs["options"]["filter"])

        self.kwargs_simulate = kwargs

        self.initialization_script = initialization_script

        self.__final = kwargs.pop("final", True)

        self.__logger = kwargs.pop("logger", False)

        # Handle results in memory. Using file can induce ambiguities.
        # If required, AssimilationPy's fmu_pool may contain a solution.
        self.kwargs_simulate.setdefault("options",
                                        dict())["result_handling"] = "memory"

    def run(self):
        """Run simulation and store results in the queue."""

        for count_retry in xrange(self.max_retry):
            try:
                simulation = simulate(self.model, **self.kwargs_simulate)
                result = strip_simulation(simulation,
                                          name_output=self.name_output,
                                          final=self.__final)
            except Exception as e:
                result = e
            else:
                break
        else:
            pass
            # A warning is issued retrospectively.

            if self.__logger:
                logger.log("Maximum number of retries reached. index=%d" %
                           self.index )
                logger.log("Input keyword arguments: index=%s" %
                           self.kwargs_simulate)

        self.queue.put([self.index, result])

#§
def threaded_function(queue, dict_result, n_result):
    """Read the values in the queue and moves them to a dictionary.

    This is function executed in the main thread. The function, and thus the
    thread, terminates when all the expected results have been read.
    The number of expected results is specified by the parameter 'n_result'.

    Parameters:
    ----------
    queue : multiprocesing.Queue,  queue containing the results generated by
    the processes.

    dict_results : dictionary for storing enqueued results.

    n_result : integer,  number of results to dequeue and move to the
    dictionary.

    """

    n = 0
    while n < n_result:
        if not queue.empty():
            index, result = queue.get()
            dict_result[index] = result
            n += 1

#§
class FMUPool():
    """Manage a pool of processes that execute parallel simulation of an FMU
    model.

    **NOTE:**
    The processes running the simulations, executed in parallel if multiple
    processors are available, produce results that are stored in a queue. If
    the queue reaches its limit the execution will be blocked until the
    resources are freed.

    """

    def __init__(self, model, n_process=None, **kwargs):
        """
        Constructor that initializes the pool of processes that runs the
        simulations.

        Parameters:
        ----------
        model : PyFMI model object.

        n_process : Integer, number of processes to run in parallel.

        """

        self.model = model
        n_process = min(n_process, multiprocessing.cpu_count() - 1)
        self.n_process_max = max(1, n_process)

        self.__logger = kwargs.pop("logger", False)


    def run(self, list_kwargs):
        """Run the simulation of the model with using parallel processes.

        Parameters:
        ----------
        list_kwargs : Sequence of dictionaries. See FMUProcess.

        """

        queue = Queue()
        n_simulation = len(list_kwargs)
        list_process = [FMUProcess(self.model, queue, ii, **kwargs) for
                        ii, kwargs in enumerate(list_kwargs)]

        if self.__logger:
            logger.log("New run. n_simulation=%d" % n_simulation)

        dict_result = {}

        # Create a Thread in the main process that will read the data from the
        # queue and put them into a dictionary. The Thread will remove
        # elements from the queue right after they have been produced, to
        # avoid to avoid reaching the size limit and block the processes.
        thread = Thread(target=threaded_function,
                        args=(queue, dict_result, n_simulation ))
        thread.daemon = True
        thread.start()

        ii = 0
        n_active = 0
        finished = False

        while not finished:
            while n_active < self.n_process_max and ii < n_simulation:
                if self.n_process_max <= 1:
                    # Just one process to run, void to do a fork.
                    # This is used when the process runs with Celery.
                    list_process[ii].run()
                else:
                    # More than one process that can be run in parallel.
                    list_process[ii].start()

                ii += 1
                n_active = len(multiprocessing.active_children())

            # 'multiprocessing.active_children()' call a .join() to every
            # children already terminated
            n_active = len(multiprocessing.active_children())

            finished = not n_active and ii == n_simulation

        thread.join(0.5)

        for key, value in dict_result.items():
            if isinstance(value, Exception):
                print "Some simulations failed."
                if self.__logger:
                    logger.log("Failed simulation with index %d (error: %s)" %
                               (key, value))

        # Sorting by keys.
        return zip(*sorted(dict_result.items(),
                           key=operator.itemgetter(0)))[1]

#§
