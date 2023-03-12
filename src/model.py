from model_base import EpidemicModelBase

import numpy as np


def get_transition_state_eq(states, val, param):
    if len(states) < 2:
        return None
    eq = dict()
    for idx, state in enumerate(states[1:], 1):
        prev_state = val[idx - 1]
        eq[state] = param * (prev_state - val[idx])
    return eq


def get_n_state_val(ps, val):
    n_state_val = dict()
    slice_start = 1
    slice_end = 1
    for comp in ["e", "i", "ic", "v"]:
        n_states = ps[f'n_{comp}_states']
        slice_end += n_states
        n_state_val[comp] = val[slice_start:slice_end]
        slice_start += n_states
    return n_state_val


class VaccinatedModel(EpidemicModelBase):
    def __init__(self, model_data):
        self.n_vac_states = model_data.model_parameters_data["n_v_states"]
        self.base_compartments = ["s", "r", "d"]
        compartments = self.base_compartments + self.get_n_compartments(model_data.model_parameters_data)
        super().__init__(model_data=model_data, compartments=compartments)
        self.sum = 0

    @staticmethod
    def get_n_states(n_classes, comp_name):
        return [f"{comp_name}_{i}" for i in range(n_classes)]

    def get_n_compartments(self, params):
        compartments = []
        for comp in ["e", "i", "ic", "v"]:
            compartments.append(self.get_n_states(comp_name=comp, n_classes=params[f'n_{comp}_states']))
        return [state for n_comp in compartments for state in n_comp]

    @staticmethod
    def get_vacc_bool(ts, ps):
        return np.array(ps["t_start"] < ts < (ps["t_start"] + ps["T"])).astype(float)

    def update_initial_values(self, iv, parameters):
        e_states = self.get_n_states(n_classes=parameters["n_e_states"], comp_name="e")
        i_states = self.get_n_states(n_classes=parameters["n_i_states"], comp_name="i")
        e = np.sum([iv[state] for state in e_states])
        i = np.sum([iv[state] for state in i_states])
        iv.update({
            "s": self.population - (e + i)
        })

    def get_model(self, xs, ts, ps, cm):

        # the same order as in self.compartments!
        val = xs.reshape(-1, self.n_age)
        n_state_val = get_n_state_val(ps, val)
        s, r, d = [val[self.c_idx[comp]] for comp in ["s", "r", "d"]]

        i = np.sum([i_state for i_state in n_state_val["i"]], axis=0)
        transmission = ps["beta_0"] * np.array(i).dot(cm)
        actual_population = self.population
        vacc = self.get_vacc_bool(ts, ps)

        model_eq_dict = {
            "s": - ps["susc"] * (s / actual_population) * transmission
                 - ps["v"] * ps["rho"] * s / (s + r) * vacc
                 + ps["psi"] * n_state_val["v"][-1],                                    # S'(t)
            "r": (1 - ps["h"] * ps["xi"]) * ps["gamma"] * n_state_val["i"][-1]
               + (1 - ps["mu"]) * ps["gamma_c"] * n_state_val["ic"][-1],                # R'(t)
            "d": ps["mu"] * ps["gamma_c"] * n_state_val["ic"][-1]                       # D'(t)
        }

        e_eq = self.get_e_eq(val=n_state_val["e"], ps=ps, s=s, transmission=transmission)
        i_eq = self.get_i_eq(n_state_val=n_state_val, ps=ps)
        ic_eq = self.get_ic_eq(n_state_val=n_state_val, ps=ps)
        v_eq = self.get_v_eq(val=n_state_val["v"], ps=ps, vacc=vacc, s=s, r=r)

        model_eq_dict.update(**e_eq, **i_eq, **ic_eq, **v_eq)
        return self.get_array_from_dict(comp_dict=model_eq_dict)

    def get_e_eq(self, val, ps, s, transmission):
        e_states = self.get_n_states(ps["n_e_states"], "e")
        actual_population = self.population
        e_eq = {"e_0": ps["susc"] * (s / actual_population) * transmission
                       - ps["alpha"] * val[0]}
        e_eq.update(get_transition_state_eq(e_states, val, ps["alpha"]))
        return e_eq

    def get_i_eq(self, n_state_val, ps):
        e_end = n_state_val["e"][-1]
        val = n_state_val["i"]
        i_states = self.get_n_states(ps["n_i_states"], "i")
        i_eq = {"i_0": ps["alpha"] * e_end - ps["gamma"] * val[0]}
        i_eq.update(get_transition_state_eq(i_states, val, ps['gamma']))
        return i_eq

    def get_ic_eq(self, n_state_val, ps):
        i_end = n_state_val["i"][-1]
        val = n_state_val["ic"]
        ic_states = self.get_n_states(ps["n_ic_states"], "ic")
        ic_eq = {"ic_0": ps["xi"] * ps["h"] * ps["gamma"] * i_end - ps["gamma_c"] * val[0]}
        ic_eq.update(get_transition_state_eq(ic_states, val, ps['gamma_c']))
        return ic_eq

    def get_v_eq(self, val, vacc, ps, s, r):
        v_states = self.get_n_states(ps["n_v_states"], "v")
        v_eq = {'v_0': ps["v"] * ps["rho"] * s / (s + r) * vacc - val[0] * ps["psi"]}
        v_eq.update(get_transition_state_eq(v_states, val, ps['psi']))
        return v_eq