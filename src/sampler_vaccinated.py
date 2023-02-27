from time import sleep

import numpy as np
from smt.sampling_methods import LHS
from tqdm import tqdm

from sampler_base import SamplerBase


class SamplerVaccinated(SamplerBase):
    def __init__(self, sim_state: dict, sim_obj):
        super().__init__(sim_state, sim_obj)
        self.sim_obj = sim_obj
        self.susc = sim_state["susc"]
        self.lhs_boundaries = {"lower": [0.1, 0.1, None, 0, 0, 0, 0],   # alpha, gamma,  beta_0, daily vaccines,
                               "upper": [1, 1, None, 1000, 100, 1, 1],  # t_start, rho, psi
                               }
        self.lhs_table = None
        self.sim_output = None
        self.param_names = self.sim_obj.data.param_names

        self.get_beta_0_boundaries()

    def run_sampling(self):
        n_samples = 10000
        bounds = np.array([bounds for bounds in self.lhs_boundaries.values()]).T
        sampling = LHS(xlimits=bounds)
        lhs_table = sampling(n_samples)
        print("Simulation for", n_samples,
              "samples (", "-".join(self._get_variable_parameters()), ")")

        target_var = self.sim_state["target_var"]
        if target_var == "r0":
            get_output = self.get_r0
        elif target_var == "infected_max":
            get_output = self.get_infected_max

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

    def get_infected_max(self, params):
        params_dict = {key: value for (key, value) in zip(self.param_names, params)}
        params = self.sim_obj.params
        params.update(params_dict)
        t = np.linspace(1, 1000, 1000)
        sol = self.sim_obj.model.get_solution(t=t, parameters=params, cm=self.sim_obj.contact_matrix)
        inf_max = np.max(sol[:, 2])
        return inf_max

    def _get_variable_parameters(self):
        return f'{self.susc}_{self.base_r0}'

    def get_beta_0_boundaries(self):
        for bound in ["lower", "upper"]:
            self.r0generator.parameters.update({"alpha": self.lhs_boundaries[bound][0],
                                                "gamma": self.lhs_boundaries[bound][1]})
            beta_0 = self.base_r0 / self.r0generator.get_eig_val(contact_mtx=self.sim_obj.contact_matrix,
                                                                 susceptibles=self.sim_obj.susceptibles.reshape(1, -1),
                                                                 population=self.sim_obj.population)[0]
            self.lhs_boundaries[bound][2] = beta_0
