# %%
from pymatgen.core import Structure
import torch
from torchsim.runners import structures_to_state
from torchsim.units import MetalUnits
from torchsim.models.mace import MaceModel
from torchsim.integrators import MDState
from torchsim.integrators import nvt_langevin
from torchsim.monte_carlo import swap_monte_carlo
from dataclasses import dataclass
from mace.calculators.foundations_models import mace_mp

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
dtype = torch.float64

kT = 1000 * MetalUnits.temperature

# Option 1: Load the raw model from the downloaded model
mace_checkpoint_url = "https://github.com/ACEsuit/mace-mp/releases/download/mace_mpa_0/mace-mpa-0-medium.model"
loaded_model = mace_mp(
    model=mace_checkpoint_url,
    return_raw_model=True,
    default_dtype=dtype,
    device=device,
)

# Option 2: Load from local file (comment out Option 1 to use this)
# MODEL_PATH = "../../../checkpoints/MACE/mace-mpa-0-medium.model"
# loaded_model = torch.load(MODEL_PATH, map_location=device)

model = MaceModel(
    model=loaded_model,
    device=device,
    dtype=dtype,
    enable_cueq=True,
)


# %%
lattice = [[5.43, 0, 0], [0, 5.43, 0], [0, 0, 5.43]]
species = ["Cu", "Cu", "Cu", "Zr", "Cu", "Zr", "Zr", "Zr"]
coords = [
    [0.0, 0.0, 0.0],
    [0.25, 0.25, 0.25],
    [0.0, 0.5, 0.5],
    [0.25, 0.75, 0.75],
    [0.5, 0.0, 0.5],
    [0.75, 0.25, 0.75],
    [0.5, 0.5, 0.0],
    [0.75, 0.75, 0.25],
]
structure = Structure(lattice, species, coords)

state = structures_to_state([structure], device=device, dtype=dtype)
state.atomic_numbers


# %%
@dataclass
class HybridSwapMCState(MDState):
    """State for Monte Carlo simulations.

    Attributes:
        energy: Energy of the system
        last_swap: Last swap attempted
    """

    last_permutation: torch.Tensor


md_state, nvt_step = nvt_langevin(state, model, dt=0.002, kT=kT, seed=42)

swap_state, swap_step = swap_monte_carlo(md_state, model, kT=kT, seed=42)

hybrid_state = HybridSwapMCState(
    **vars(md_state),
    last_permutation=torch.zeros(
        md_state.n_batches, device=md_state.device, dtype=torch.bool
    ),
)

og_hybrid_state = hybrid_state.clone()

generator = torch.Generator(device=device)
generator.manual_seed(42)

for i in range(100):
    if i % 10 == 0:
        hybrid_state = swap_step(hybrid_state, kT=torch.tensor(kT), generator=generator)
    else:
        hybrid_state = nvt_step(hybrid_state, dt=torch.tensor(0.002), kT=torch.tensor(kT))
