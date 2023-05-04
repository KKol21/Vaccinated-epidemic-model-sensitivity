import torch
from torchdiffeq import odeint

from src.model.eqns_generator import EquationGenerator
from src.model.model_base import EpidemicModelBase


def get_n_states(n_classes, comp_name):
    return [f"{comp_name}_{i}" for i in range(n_classes)]


class VaccinatedModel(EpidemicModelBase):
    def __init__(self, model_data):
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.n_state_comp = ["e", "i", "h", "ic", "icr", "v"]
        compartments = ["s"] + self.get_n_compartments(model_data.model_parameters_data) + ["r", "d"]
        super().__init__(model_data=model_data, compartments=compartments)
        self.eq_solver = EquationGenerator(ps=model_data.model_parameters_data,
                                           actual_population=self.population)

    def get_model(self, ts, xs, ps, cm):

        val = xs.reshape(-1, self.n_age)
        n_state_val = self.get_n_state_val(ps, val)
        # the same order as in self.compartments!
        s = val[0]
        r = val[-2]
        i = torch.stack([i_state for i_state in n_state_val["i"]]).sum(0)
        transmission = ps["beta"] * i.matmul(cm)
        vacc = self.get_vacc_bool(ts, ps)

        model_eq = self.eq_solver.evaluate_eqns(n_state_val=n_state_val, s=s, r=r,
                                                transmission=transmission, vacc=vacc)
        return torch.cat(tuple(model_eq))

    def get_n_compartments(self, params):
        compartments = []
        for comp in self.n_state_comp:
            compartments.append(get_n_states(comp_name=comp, n_classes=params[f'n_{comp}']))
        return [state for n_comp in compartments for state in n_comp]

    def get_n_state_val(self, ps, val):
        n_state_val = dict()
        slice_start = 1
        slice_end = 1
        for comp in self.n_state_comp:
            n_states = ps[f'n_{comp}']
            slice_end += n_states
            n_state_val[comp] = val[slice_start:slice_end]
            slice_start += n_states
        return n_state_val

    def update_initial_values(self, iv, parameters):
        iv["e_0"][2] = 1
        e_states = get_n_states(n_classes=parameters["n_e"], comp_name="e")
        i_states = get_n_states(n_classes=parameters["n_i"], comp_name="i")
        e = torch.stack([iv[state] for state in e_states]).sum(0)
        i = torch.stack([iv[state] for state in i_states]).sum(0)
        iv.update({
            "s": self.population - (e + i)
        })

    def get_solution_torch(self, t, parameters, cm):
        initial_values = self.get_initial_values(parameters)
        model_wrapper = ModelFun(self, parameters, cm).to(self.device)
        return odeint(model_wrapper.forward, initial_values, t, method='euler')

    def get_solution_torch_test(self, t, param, cm):
        initial_values = self.get_initial_values(param)
        model_wrapper = ModelEq(self, param, cm)
        return odeint(model_wrapper.forward, initial_values, t, method='euler')

    @staticmethod
    def get_vacc_bool(ts, ps):
        return int(ps["t_start"] < ts < (ps["t_start"] + ps["T"]))


class ModelFun(torch.nn.Module):
    """
    Wrapper class for VaccinatedModel.get_model. Inherits from torch.nn.Module, enabling
    the use of a GPU for evaluation through the library torchdiffeq.
    """
    def __init__(self, model, ps, cm):
        super(ModelFun, self).__init__()
        self.model = model
        self.ps = ps
        self.cm = cm

    def forward(self, ts, xs):
        return self.model.get_model(ts, xs, self.ps, self.cm)


class ModelEq(torch.nn.Module):
    def __init__(self, model: VaccinatedModel, ps: dict, cm: torch.Tensor):
        super(ModelEq, self).__init__()
        self.model = model
        self.cm = cm
        self.ps = ps

        self.c_idx = model.c_idx
        self.n_age = model.n_age
        self.n_comp = len(model.compartments)
        self.s_mtx = self.n_age * self.n_comp
        self.diag_idx = (range(self.n_age), range(self.n_age))

        self._get_trans_param_dict()
        self._get_A()
        self._get_transmission()
        self._get_B()

    # For increased efficiency, we represent the ODE system in the form
    # y' = (A @ y) * (T @ y) + B @ y,
    # saving every tensor in the module state
    def forward(self, t, y: torch.Tensor) -> torch.Tensor:
        # Fill in elements in B corresponding to vaccination
        if t == 1:
            v = self.ps["v"]
            # S  ->  V
            self.B[self.get_diag_slice('v_0'), self.get_diag_slice('s')] = v
            self.B[self.get_diag_slice('s'), self.get_diag_slice('s')] = - self.ps["psi"] * v
            # V  ->  S
            self.B[self.get_diag_slice('s'), self.get_diag_slice('v_0')] = self.ps["psi"] * v
        return torch.matmul(self.A @ y, self.T @ y) + self.B @ y

    def _get_A(self):
        # When multiplied by y, gives back the 1D tensor containing the rate of transmission at the indexes
        # of compartments s and e_0, corresponding to the age groups of those compartments
        A = torch.zeros((self.s_mtx, self.s_mtx))
        n_age = self.n_age
        transmission_rate = self.ps["beta"] * self.ps["susc"] / self.model.population
        indices = self.get_diag_slice('s')
        diag_idx = (range(n_age), range(n_age))

        A[indices, indices][diag_idx] = - transmission_rate
        A[self.get_diag_slice('e_0'), indices][diag_idx] = transmission_rate
        self.A = A

    def _get_transmission(self):
        n_i = self.ps["n_i"]
        T_r = torch.zeros((self.n_age * n_i, self.s_mtx))
        # When multiplied with y gives the 1D tensor, where it's ith element corresponds to the contact
        # of the (i % n_i)th infected state from every infected age group with age group (i // n_age)
        for idx, i_state in enumerate(get_n_states(n_i, "i")):
            T_r[idx:self.n_age * n_i:n_i, self.get_diag_slice(i_state)] = self.cm

        T_l = torch.zeros((self.s_mtx, self.n_age * n_i))
        # When matrix multiplied with T_r @ y, we get a 1D tensor, where it's ith element is
        # the sum of all contacts of any infecteds with age group (i // n_age)
        for idx, i_state in enumerate(get_n_states(n_i, "i")):
            T_l[self.get_diag_slice('s'), idx:self.n_age * n_i][self.diag_idx] = 1
            T_l[self.get_diag_slice('e_0'), idx:self.n_age * n_i][self.diag_idx] = 1
        self.T = T_l @ T_r

    def _get_B(self):
        from src.model.r0 import generate_transition_block
        # Tensor representing the first order elements of the ODE system
        s_mtx = self.s_mtx
        B = torch.zeros((s_mtx, s_mtx))
        c_idx = self.c_idx
        ps = self.ps
        diag_slice = self.get_diag_slice
        # We begin by filling in the transition blocks for the erlang distributed parameters
        for age_group in range(self.n_age):
            for comp, trans_param in self.trans_param_dict.items():
                diag_idx = age_group * self.n_comp + c_idx[f'{comp}_0']
                block_slice = slice(diag_idx, diag_idx + ps[f'n_{comp}'])
                B[block_slice, block_slice] = generate_transition_block(trans_param, ps[f'n_{comp}'])
        # Then do the rest of the first order terms, except for the elements dependent on the vaccination parameters
        c_end = self.get_end_state
        i_end = c_end('i')
        h_end = c_end('h')
        ic_end = c_end('ic')
        icr_end = c_end('icr')
        v_end = c_end('v')

        # I   ->  H
        B[diag_slice('h_0'), diag_slice(i_end)] = (1 - ps["xi"]) * ps["h"] * ps["gamma"]
        # H   ->  R
        B[diag_slice('r'), diag_slice(h_end)] = ps['gamma_h']
        # I   ->  IC
        B[diag_slice('ic_0'), diag_slice(i_end)] = ps["xi"] * ps["h"] * ps["gamma"]
        # IC  ->  ICR
        B[diag_slice('icr_0'), diag_slice(ic_end)] = ps["gamma_c"] * (1 - ps["mu"])
        # ICR ->  R
        B[diag_slice('r'), diag_slice(icr_end)] = ps["gamma_cr"]
        # IC  ->  D
        B[diag_slice('d'), diag_slice(ic_end)] = ps["gamma_c"] * ps["mu"]
        # I   ->  R
        B[diag_slice('r'), diag_slice(i_end)] = (1 - ps['h']) * ps['gamma']
        self.B = B

    def _get_trans_param_dict(self):
        ps = self.ps
        trans_param_list = [ps["alpha"], ps["gamma"], ps["gamma_h"], ps["gamma_c"], ps["gamma_cr"], ps["psi"]]
        self.trans_param_dict = {key: value for key, value in zip(self.model.n_state_comp, trans_param_list)}

    def get_diag_slice(self, comp: str) -> slice:
        return slice(self.c_idx[comp], self.s_mtx, self.n_comp)

    def get_end_state(self, comp: str) -> str:
        n_states = self.ps[f'n_{comp}']
        return f'i_{n_states - 1}'

    @staticmethod
    def get_vacc_bool(ts, ps):
        return int(ps["t_start"] < ts < (ps["t_start"] + ps["T"]))
