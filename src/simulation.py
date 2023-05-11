import itertools
import os

import numpy as np
import torch

from src.dataloader import DataLoader
from src.model.model import VaccinatedModel
from src.sensitivity.prcc import get_prcc_values
from src.model.r0 import R0Generator
from src.sensitivity.sampler_vaccinated import SamplerVaccinated
from src.plotter import generate_prcc_plot, generate_epidemic_plot, generate_epidemic_plot_


class SimulationVaccinated:
    def __init__(self):
        # Load data
        self.data = DataLoader()
        self.test = True

        # User-defined parameters
        self.susc_choices = [1.0]
        self.r0_choices = [1.8, 2.4, 3]
        self.target_var_choices = ["d_max", "i_max", "ic_max"]  # i_max, ic_max, d_max
        self.distr = "erlang"  # Distribution of time spent in exposed/infected state

        # Define initial configs
        self._get_initial_config()

    # Generate samples and run simulations, then save the result
    def run_sampling(self):
        susceptibility = torch.ones(self.n_age).to(self.data.device)
        simulations = itertools.product(self.susc_choices, self.r0_choices, self.target_var_choices)
        for susc, base_r0, target_var in simulations:
            susceptibility[:4] = susc
            self.params.update({"susc": susceptibility})
            r0generator = R0Generator(param=self.params, device=self.data.device, n_age=self.n_age)
            # Calculate base transmission rate
            beta = base_r0 / r0generator.get_eig_val(contact_mtx=self.contact_matrix,
                                                     susceptibles=self.susceptibles.reshape(1, -1),
                                                     population=self.population)
            self.params.update({"beta": beta})
            sim_state = {"base_r0": base_r0, "susc": susc, "r0generator": r0generator,
                         "target_var": target_var}
            param_generator = SamplerVaccinated(sim_state=sim_state, sim_obj=self)
            param_generator.run_sampling()

    # Calculate PRCC values from saved LHS tables, then save the result
    def calculate_prcc(self):
        os.makedirs(f'../sens_data/prcc', exist_ok=True)
        simulations = itertools.product(self.susc_choices, self.r0_choices, self.target_var_choices)
        for susc, base_r0, target_var in simulations:
            filename = f'{susc}-{base_r0}-{target_var}-{self.distr}'
            lhs_table = np.loadtxt(f'../sens_data/lhs/lhs_{filename}.csv', delimiter=';')
            sim_output = np.loadtxt(f'../sens_data/simulations/simulations_{filename}.csv', delimiter=';')

            prcc = get_prcc_values(np.c_[lhs_table, sim_output.T])
            np.savetxt(fname=f'../sens_data/prcc/prcc_{filename}.csv', X=prcc)

    # Create and save tornado plots from sensitivity data
    def plot_prcc(self):
        os.makedirs(f'../sens_data//prcc_plots', exist_ok=True)
        simulations = itertools.product(self.susc_choices, self.r0_choices, self.target_var_choices)
        for susc, base_r0, target_var in simulations:
            filename = f'{susc}-{base_r0}-{target_var}-{self.distr}'
            prcc = np.loadtxt(fname=f'../sens_data/prcc/prcc_{filename}.csv')

            generate_prcc_plot(params=self.data.param_names,
                               target_var=target_var,
                               prcc=prcc,
                               filename=filename,
                               r0=base_r0)

    # Create and save epidemic plots corresponding to the most optimal vaccine distributions from LHS sampling
    def plot_optimal_vaccine_distributions(self):
        os.makedirs(f'../sens_data//epidemic_plots', exist_ok=True)
        simulations = itertools.product(self.susc_choices, self.r0_choices, self.target_var_choices)
        for susc, base_r0, target_var in simulations:
            filename = f'{susc}-{base_r0}-{target_var}-{self.distr}'
            vaccination = np.loadtxt(fname=f'../sens_data/optimal_vaccination/optimal_vaccination_{filename}.csv')
            generate_epidemic_plot(self, vaccination, filename, target_var, base_r0, compartments=["ic", "d"])

    def plot_subopt(self):
        os.makedirs('../sens_data//epidemic_plots_', exist_ok=True)
        target_var = 'ic_max'
        r0 = 1.8
        r0_bad = 3
        filename = f'1.0-{r0_bad}-{target_var}-{self.distr}'
        filename_opt = f'1.0-{r0}-{target_var}-{self.distr}'
        vaccination = np.loadtxt(fname=f'../sens_data/optimal_vaccination/optimal_vaccination_{filename}.csv')
        vaccination_opt = np.loadtxt(fname=f'../sens_data/optimal_vaccination/optimal_vaccination_{filename_opt}.csv')
        generate_epidemic_plot_(sim_obj=self,
                                vaccination=vaccination,
                                vaccination_opt=vaccination_opt,
                                filename=filename,
                                target_var=target_var,
                                r0=r0,
                                r0_bad=r0_bad,
                                compartments=['ic'])

    def _get_initial_config(self):
        self.params = self.data.model_parameters_data
        self.n_age = self.data.contact_data["home"].shape[0]
        self.contact_matrix = self.data.contact_data["home"] + self.data.contact_data["work"] + \
            self.data.contact_data["school"] + self.data.contact_data["other"]
        self.model = VaccinatedModel(model_data=self.data, cm=self.contact_matrix)
        self.population = self.model.population
        self.age_vector = self.population.reshape((-1, 1))
        self.susceptibles = self.model.get_initial_values()[self.model.idx("s")]
        self.device = self.data.device

