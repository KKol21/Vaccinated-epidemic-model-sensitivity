from functools import partial
from time import sleep

import numpy as np
from smt.sampling_methods import LHS
import torch
from tqdm import tqdm

from sampler_base import SamplerBase


class SamplerVaccinated(SamplerBase):
    def __init__(self, sim_state: dict, sim_obj):
        super().__init__(sim_state, sim_obj)
        self.sim_obj = sim_obj
        self.susc = sim_state["susc"]
        self.lhs_boundaries = {"lower": np.zeros(sim_obj.no_ag),    # Number of daily vaccines per age group
                               "upper": np.full(fill_value=2000, shape=sim_obj.no_ag)
                               }
        self.lhs_table = None
        self.sim_output = None
        self.param_names = self.sim_obj.data.param_names

    def run_sampling(self):
        n_samples = 500
        bounds = np.array([bounds for bounds in self.lhs_boundaries.values()]).T
        sampling = LHS(xlimits=bounds)
        lhs_table = sampling(n_samples)
        print("Simulation for", n_samples,
              "samples (", "-".join(self._get_variable_parameters()), ")")

        target_var = self.sim_state["target_var"]
        if target_var == "r0":
            get_output = self.get_r0
        else:
            get_output = partial(self.get_max, comp=target_var.split('_')[0])
        results = list(tqdm(map(get_output, lhs_table), total=lhs_table.shape[0]))
        results = np.array(results)
        # Sort tables by R0 values
        sorted_idx = results.argsort()
        results = results[sorted_idx]
        lhs_table = np.array(lhs_table[sorted_idx])
        sim_output = np.array(results)
        sleep(0.3)

        self._save_output(output=lhs_table, folder_name='lhs')
        self._save_output(output=sim_output, folder_name='simulations')

    def get_r0(self, params):
        params_dict = {key: value for (key, value) in zip(self.param_names, params)}
        self.r0generator.parameters.update(params_dict)
        r0_lhs = params[2] * self.r0generator.get_eig_val(contact_mtx=self.sim_obj.contact_matrix,
                                                          susceptibles=self.sim_obj.susceptibles.reshape(1, -1),
                                                          population=self.sim_obj.population)[0]
        return r0_lhs

    def get_max(self, params, comp):
        parameters = self.sim_obj.params
        parameters.update({'daily_vaccines': params})

        t = torch.linspace(1, 200, 200)
        sol = self.sim_obj.model.get_solution_torch(t=t, parameters=parameters, cm=self.sim_obj.contact_matrix)
        if self.sim_obj.test:
            if abs(self.sim_obj.population.sum() - sol[-1, :].sum()) > 100:
                raise Exception("Unexpected change in population size")

        if comp in self.sim_obj.model.n_state_comp:
            n_states = parameters[f"n_{comp}_states"]
            idx_start = self.sim_obj.model.n_age * (self.sim_obj.model.c_idx[f"{comp}_0"])
        else:
            n_states = 1
            idx_start = self.sim_obj.model.n_age * self.sim_obj.model.c_idx[comp]

        comp_max = torch.max(self.sim_obj.model.aggregate_by_age(solution=sol, idx=idx_start, n_states=n_states))
        return comp_max

    def _get_variable_parameters(self):
        return f'{self.susc}_{self.base_r0}'
