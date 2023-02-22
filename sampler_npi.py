from time import sleep

import numpy as np
from smt.sampling_methods import LHS
from tqdm import tqdm

from sampler_base import SamplerBase


class VaccinatedSampler(SamplerBase):
    def __init__(self, sim_state: dict, sim_obj):
        super().__init__(sim_state, sim_obj)
        self.sim_obj = sim_obj
        self.susc = sim_state["susc"]
        self.lhs_boundaries = {"lower": [0.1, 0.1, None, 0, 0, 0, 0, 0],     # alpha, gamma,  beta_0, daily vaccines,
                               "upper": [1, 1, None, 1000, 100, 120, 1, 1],  # t_start, T, rho, psi
                               }
        self.lhs_table = None
        self.sim_output = None

        self.get_beta_0_boundaries()

    def run(self):
        n_samples = 1000
        bounds = np.array([bounds for bounds in self.lhs_boundaries.values()]).T
        sampling = LHS(xlimits=bounds)
        lhs_table = sampling(n_samples)
        print("Simulation for", n_samples,
              "samples (", "-".join(self._get_variable_parameters()), ")")

        results = list(tqdm(map(self.get_output, lhs_table), total=lhs_table.shape[0]))
        results = np.array(results)

        # Sort tables by R0 values
        sorted_idx = results.argsort()
        results = results[sorted_idx]
        lhs_table = np.array(lhs_table[sorted_idx])
        sim_output = np.array(results)
        sleep(0.3)

        self.lhs_table = lhs_table
        self.sim_output = sim_output

    def get_output(self, params: np.ndarray):
        param_names = ["alpha", "gamma", "beta_0", "daily_vaccines", "t_start", "T", "rho", "psi"]
        params_dict = {key: value for (key, value) in zip(param_names, params)}
        self.r0generator.parameters.update(params_dict)
        r0_lhs = params[2] * self.r0generator.get_eig_val(contact_mtx=self.sim_obj.contact_matrix,
                                                          susceptibles=self.sim_obj.susceptibles.reshape(1, -1),
                                                          population=self.sim_obj.population)[0]
        return r0_lhs

    def _get_variable_parameters(self):
        return [str(self.susc), str(self.base_r0)]

    def get_beta_0_boundaries(self):
        for bound in ["lower", "upper"]:
            self.r0generator.parameters.update({"alpha": self.lhs_boundaries[bound][0],
                                                "gamma": self.lhs_boundaries[bound][1]})
            beta_0 = self.base_r0 / self.r0generator.get_eig_val(contact_mtx=self.sim_obj.contact_matrix,
                                                                 susceptibles=self.sim_obj.susceptibles.reshape(1, -1),
                                                                 population=self.sim_obj.population)[0]
            self.lhs_boundaries[bound][2] = beta_0
