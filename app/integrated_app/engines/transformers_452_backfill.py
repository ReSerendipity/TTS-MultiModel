# SPDX-FileCopyrightText: 2026 ReSerendipity
# SPDX-License-Identifier: Apache-2.0
"""transformers 4.52.1 符号库——indextts vendored 副本依赖的旧符号回填。

indextts 的 vendored transformers 副本（transformers_generation_utils.py /
transformers_modeling_utils.py / transformers_gpt2.py / transformers_beam_search.py）
按 4.52.1 API 开发，其顶层导入在 5.20.2 下缺 18 个符号（AST 全量扫描实证）。
本模块把这些符号的 4.52.1 原始定义（Apache-2.0，HuggingFace Inc. 团队）按需
回填到对应模块，由 `indextts_tf_compat.apply()` 统一调用 `install()`。

纪律：
- 全部 `hasattr` 守卫，旧版本下自动 no-op；
- 每组标注来源版本与用途；TTF/Flax 相关大函数以 stub 抛 ImportError
  （TTS 推理路径永不触达，装齐 TF/Flax 也不该再走 5.x 的已删除通道）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("tts_multimodel")

# ---- pre-stub：后续块依赖的 utils 符号（先于块定义注入，官方 5.x 已移除/搬家）----
import transformers.utils as _tu_pre

if not hasattr(_tu_pre, "is_offline_mode"):
    def is_offline_mode():  # noqa: D401 - 4.52 hub.py 同名函数语义
        import huggingface_hub.constants as _hfc

        return _hfc.HF_HUB_OFFLINE

    setattr(_tu_pre, "is_offline_mode", is_offline_mode)


def _ensure_module(dotted):
    """官方模块存在则返回；整体被移除（如 beam_constraints）则动态创建并挂到父包。"""
    import importlib
    import sys
    import types

    try:
        return importlib.import_module(dotted)
    except ModuleNotFoundError:
        parent, _, leaf = dotted.rpartition(".")
        parent_mod = importlib.import_module(parent) if parent else None
        mod = types.ModuleType(dotted)
        mod.__package__ = parent
        sys.modules[dotted] = mod
        if parent_mod is not None:
            setattr(parent_mod, leaf, mod)
        return mod


# ==================== pytorch_utils.py  (4.52 prune/heads 工具（5.x 随 head-pruning 废弃移除）) ====================

import inspect
from functools import lru_cache, wraps
from typing import Callable
import torch
from safetensors.torch import storage_ptr, storage_size
from torch import nn
from transformers.utils import is_torch_greater_or_equal, is_torch_xla_available, is_torchdynamo_compiling, logging

# 4.52.1 `utils/import_utils.py` 的模块级常量（5.x 只保留函数形式）
is_torch_greater_or_equal_than_2_4 = is_torch_greater_or_equal("2.4")


def prune_linear_layer(layer: nn.Linear, index: torch.LongTensor, dim: int = 0) -> nn.Linear:
    """
    Prune a linear layer to keep only entries in index.

    Used to remove heads.

    Args:
        layer (`torch.nn.Linear`): The layer to prune.
        index (`torch.LongTensor`): The indices to keep in the layer.
        dim (`int`, *optional*, defaults to 0): The dimension on which to keep the indices.

    Returns:
        `torch.nn.Linear`: The pruned layer as a new layer with `requires_grad=True`.
    """
    index = index.to(layer.weight.device)
    W = layer.weight.index_select(dim, index).detach().clone()
    if layer.bias is not None:
        if dim == 1:
            b = layer.bias.detach().clone()
        else:
            b = layer.bias[index].detach().clone()
    new_size = list(layer.weight.size())
    new_size[dim] = len(index)
    new_layer = nn.Linear(new_size[1], new_size[0], bias=layer.bias is not None).to(layer.weight.device)
    new_layer.weight.requires_grad = False
    new_layer.weight.copy_(W.contiguous())
    new_layer.weight.requires_grad = True
    if layer.bias is not None:
        new_layer.bias.requires_grad = False
        new_layer.bias.copy_(b.contiguous())
        new_layer.bias.requires_grad = True
    return new_layer

class Conv1D(nn.Module):
    """
    1D-convolutional layer as defined by Radford et al. for OpenAI GPT (and also used in GPT-2).

    Basically works like a linear layer but the weights are transposed.

    Args:
        nf (`int`): The number of output features.
        nx (`int`): The number of input features.
    """

    def __init__(self, nf, nx):
        super().__init__()
        self.nf = nf
        self.nx = nx
        self.weight = nn.Parameter(torch.empty(nx, nf))
        self.bias = nn.Parameter(torch.zeros(nf))
        nn.init.normal_(self.weight, std=0.02)

    def __repr__(self) -> str:
        return "Conv1D(nf={nf}, nx={nx})".format(**self.__dict__)

    def forward(self, x):
        size_out = x.size()[:-1] + (self.nf,)
        x = torch.addmm(self.bias, x.view(-1, x.size(-1)), self.weight)
        x = x.view(size_out)
        return x

def prune_conv1d_layer(layer: Conv1D, index: torch.LongTensor, dim: int = 1) -> Conv1D:
    """
    Prune a Conv1D layer to keep only entries in index. A Conv1D work as a Linear layer (see e.g. BERT) but the weights
    are transposed.

    Used to remove heads.

    Args:
        layer ([`~pytorch_utils.Conv1D`]): The layer to prune.
        index (`torch.LongTensor`): The indices to keep in the layer.
        dim (`int`, *optional*, defaults to 1): The dimension on which to keep the indices.

    Returns:
        [`~pytorch_utils.Conv1D`]: The pruned layer as a new layer with `requires_grad=True`.
    """
    index = index.to(layer.weight.device)
    W = layer.weight.index_select(dim, index).detach().clone()
    if dim == 0:
        b = layer.bias.detach().clone()
    else:
        b = layer.bias[index].detach().clone()
    new_size = list(layer.weight.size())
    new_size[dim] = len(index)
    new_layer = Conv1D(new_size[1], new_size[0]).to(layer.weight.device)
    new_layer.weight.requires_grad = False
    new_layer.weight.copy_(W.contiguous())
    new_layer.weight.requires_grad = True
    new_layer.bias.requires_grad = False
    new_layer.bias.copy_(b.contiguous())
    new_layer.bias.requires_grad = True
    return new_layer

def prune_layer(layer: nn.Linear | Conv1D, index: torch.LongTensor, dim: int | None = None) -> nn.Linear | Conv1D:
    """
    Prune a Conv1D or linear layer to keep only entries in index.

    Used to remove heads.

    Args:
        layer (`Union[torch.nn.Linear, Conv1D]`): The layer to prune.
        index (`torch.LongTensor`): The indices to keep in the layer.
        dim (`int`, *optional*): The dimension on which to keep the indices.

    Returns:
        `torch.nn.Linear` or [`~pytorch_utils.Conv1D`]: The pruned layer as a new layer with `requires_grad=True`.
    """
    if isinstance(layer, nn.Linear):
        return prune_linear_layer(layer, index, dim=0 if dim is None else dim)
    elif isinstance(layer, Conv1D):
        return prune_conv1d_layer(layer, index, dim=1 if dim is None else dim)
    else:
        raise ValueError(f"Can't prune layer of class {layer.__class__}")

def find_pruneable_heads_and_indices(
    heads: list[int], n_heads: int, head_size: int, already_pruned_heads: set[int]
) -> tuple[set[int], torch.LongTensor]:
    """
    Finds the heads and their indices taking `already_pruned_heads` into account.

    Args:
        heads (`List[int]`): List of the indices of heads to prune.
        n_heads (`int`): The number of heads in the model.
        head_size (`int`): The size of each head.
        already_pruned_heads (`Set[int]`): A set of already pruned heads.

    Returns:
        `Tuple[Set[int], torch.LongTensor]`: A tuple with the indices of heads to prune taking `already_pruned_heads`
        into account and the indices of rows/columns to keep in the layer weight.
    """
    mask = torch.ones(n_heads, head_size)
    heads = set(heads) - already_pruned_heads  # Convert to set and remove already pruned heads
    for head in heads:
        # Compute how many pruned heads are before the head and move the index accordingly
        head = head - sum(1 if h < head else 0 for h in already_pruned_heads)
        mask[head] = 0
    mask = mask.view(-1).contiguous().eq(1)
    index: torch.LongTensor = torch.arange(len(mask))[mask].long()
    return heads, index

def isin_mps_friendly(elements: torch.Tensor, test_elements: torch.Tensor | int) -> torch.Tensor:
    """
    Same as `torch.isin` without flags, but MPS-friendly. We can remove this function when we stop supporting
    torch <= 2.3. See https://github.com/pytorch/pytorch/issues/77764#issuecomment-2067838075

    Args:
        elements (`torch.Tensor`): Input elements
        test_elements (`torch.Tensor` or `int`): The elements to check against.

    Returns:
        `torch.Tensor`: A boolean tensor of the same shape as `elements` that is True for `elements` in `test_elements`
        and False otherwise
    """

    if elements.device.type == "mps" and not is_torch_greater_or_equal_than_2_4:
        test_elements = torch.tensor(test_elements)
        if test_elements.ndim == 0:
            test_elements = test_elements.unsqueeze(0)
        return elements.tile(test_elements.shape[0], 1).eq(test_elements.unsqueeze(1)).sum(dim=0).bool().squeeze()
    else:
        # Note: don't use named arguments in `torch.isin`, see https://github.com/pytorch/pytorch/issues/126045
        return torch.isin(elements, test_elements)


# ==================== generation/beam_constraints.py  (4.52 beam constraints（5.x 整体移除）) ====================
from abc import ABC, abstractmethod
from typing import List, Optional


class Constraint(ABC):
    r"""Abstract base class for all constraints that can be applied during generation.
    It must define how the constraint can be satisfied.

    All classes that inherit Constraint must follow the requirement that

    ```py
    completed = False
    while not completed:
        _, completed = constraint.update(constraint.advance())
    ```

    will always terminate (halt).
    """

    def __init__(self):
        # test for the above condition
        self.test()

    def test(self):
        """
        Tests whether this constraint has been properly defined.
        """
        counter = 0
        completed = False
        while not completed:
            if counter == 1:
                self.reset()
            advance = self.advance()
            if not self.does_advance(advance):
                raise Exception(
                    "Custom Constraint is not defined correctly. self.does_advance(self.advance()) must be true."
                )

            stepped, completed, reset = self.update(advance)
            counter += 1

            if counter > 10000:
                raise Exception("update() does not fulfill the constraint.")

        if self.remaining() != 0:
            raise Exception("Custom Constraint is not defined correctly.")

    @abstractmethod
    def advance(self):
        """
        When called, returns the token(s) that would take this constraint one step closer to being fulfilled.

        Return:
            token_ids (Union[int, List[int], None]):
                - A single token ID (int) that advances the constraint, or
                - A list of token IDs that could advance the constraint
                - None if the constraint is completed or cannot be advanced
        """
        raise NotImplementedError(
            f"{self.__class__} is an abstract class. Only classes inheriting this class can be called."
        )

    @abstractmethod
    def does_advance(self, token_id: int):
        """
        Reads in a token and returns whether it creates progress.
        """
        raise NotImplementedError(
            f"{self.__class__} is an abstract class. Only classes inheriting this class can be called."
        )

    @abstractmethod
    def update(self, token_id: int):
        """
        Reads in a token and returns booleans that indicate the progress made by it. This function will update the
        state of this object unlikes `does_advance(self, token_id: int)`.

        This isn't to test whether a certain token will advance the progress; it's to update its state as if it has
        been generated. This becomes important if token_id != desired token (refer to else statement in
        PhrasalConstraint)

        Args:
            token_id(`int`):
                The id of a newly generated token in the beam search.
        Return:
            stepped(`bool`):
                Whether this constraint has become one step closer to being fulfuilled.
            completed(`bool`):
                Whether this constraint has been completely fulfilled by this token being generated.
            reset (`bool`):
                Whether this constraint has reset its progress by this token being generated.
        """
        raise NotImplementedError(
            f"{self.__class__} is an abstract class. Only classes inheriting this class can be called."
        )

    @abstractmethod
    def reset(self):
        """
        Resets the state of this constraint to its initialization. We would call this in cases where the fulfillment of
        a constraint is abrupted by an unwanted token.
        """
        raise NotImplementedError(
            f"{self.__class__} is an abstract class. Only classes inheriting this class can be called."
        )

    @abstractmethod
    def remaining(self):
        """
        Returns the number of remaining steps of `advance()` in order to complete this constraint.
        """
        raise NotImplementedError(
            f"{self.__class__} is an abstract class. Only classes inheriting this class can be called."
        )

    @abstractmethod
    def copy(self, stateful=False):
        """
        Creates a new instance of this constraint.

        Args:
            stateful(`bool`): Whether to not only copy the constraint for new instance, but also its state.

        Return:
            constraint(`Constraint`): The same constraint as the one being called from.
        """
        raise NotImplementedError(
            f"{self.__class__} is an abstract class. Only classes inheriting this class can be called."
        )

class PhrasalConstraint(Constraint):
    r"""
    [`Constraint`] enforcing that an ordered sequence of tokens is included in the output.

    Args:
        token_ids (`List[int]`):
            The id of the token that must be generated by the output.
    """

    def __init__(self, token_ids: List[int]):
        super(Constraint, self).__init__()

        if not isinstance(token_ids, list) or len(token_ids) == 0:
            raise ValueError(f"`token_ids` has to be a non-empty list, but is {token_ids}.")
        if any((not isinstance(token_id, int) or token_id < 0) for token_id in token_ids):
            raise ValueError(f"Each list in `token_ids` has to be a list of positive integers, but is {token_ids}.")

        self.token_ids = token_ids

        self.seqlen = len(self.token_ids)
        self.fulfilled_idx = -1  # the index of the currently fulfilled step
        self.completed = False

    def advance(self):
        if self.completed:
            return None
        return self.token_ids[self.fulfilled_idx + 1]

    def does_advance(self, token_id: int):
        if not isinstance(token_id, int):
            raise TypeError(f"`token_id` has to be an `int`, but is {token_id} of type {type(token_id)}")

        if self.completed:
            return False

        return token_id == self.token_ids[self.fulfilled_idx + 1]

    def update(self, token_id: int):
        if not isinstance(token_id, int):
            raise TypeError(f"`token_id` has to be an `int`, but is {token_id} of type {type(token_id)}")

        stepped = False
        completed = False
        reset = False

        if self.does_advance(token_id):
            self.fulfilled_idx += 1
            stepped = True
            if self.fulfilled_idx == (self.seqlen - 1):
                completed = True
            self.completed = completed
        else:
            # failed to make progress.
            reset = True
            self.reset()
        return stepped, completed, reset

    def reset(self):
        self.completed = False
        self.fulfilled_idx = 0

    def remaining(self):
        return self.seqlen - (self.fulfilled_idx + 1)

    def copy(self, stateful=False):
        new_constraint = PhrasalConstraint(self.token_ids)

        if stateful:
            new_constraint.seq_len = self.seqlen
            new_constraint.fulfilled_idx = self.fulfilled_idx
            new_constraint.completed = self.completed

        return new_constraint

class DisjunctiveTrie:
    def __init__(self, nested_token_ids: List[List[int]], no_subsets=True):
        r"""
        A helper class that builds a trie with the words represented in `nested_token_ids`.
        """
        self.max_height = max([len(one) for one in nested_token_ids])

        root = {}
        for token_ids in nested_token_ids:
            level = root
            for tidx, token_id in enumerate(token_ids):
                if token_id not in level:
                    level[token_id] = {}

                level = level[token_id]

        if no_subsets and self.has_subsets(root, nested_token_ids):
            raise ValueError(
                "Each list in `nested_token_ids` can't be a complete subset of another list, but is"
                f" {nested_token_ids}."
            )

        self.trie = root

    def next_tokens(self, current_seq):
        """
        The next possible tokens that will progress the trie, given the current sequence of tokens in `current_seq`.
        """
        start = self.trie

        for current_token in current_seq:
            start = start[current_token]

        next_tokens = list(start.keys())

        return next_tokens

    def reached_leaf(self, current_seq):
        next_tokens = self.next_tokens(current_seq)

        return len(next_tokens) == 0

    def count_leaves(self, root):
        next_nodes = list(root.values())
        if len(next_nodes) == 0:
            return 1
        else:
            return sum([self.count_leaves(nn) for nn in next_nodes])

    def has_subsets(self, trie, nested_token_ids):
        """
        Returns whether # of leaves == # of words. Otherwise some word is a subset of another.
        """
        leaf_count = self.count_leaves(trie)
        return len(nested_token_ids) != leaf_count

class DisjunctiveConstraint(Constraint):
    r"""
    A special [`Constraint`] that is fulfilled by fulfilling just one of several constraints.

    Args:
        nested_token_ids (`List[List[int]]`):
            A list of words, where each word is a list of ids. This constraint is fulfilled by generating just one from
            the list of words.
    """

    def __init__(self, nested_token_ids: List[List[int]]):
        super(Constraint, self).__init__()

        if not isinstance(nested_token_ids, list) or len(nested_token_ids) == 0:
            raise ValueError(f"`nested_token_ids` has to be a non-empty list, but is {nested_token_ids}.")
        if any(not isinstance(token_ids, list) for token_ids in nested_token_ids):
            raise ValueError(f"`nested_token_ids` has to be a list of lists, but is {nested_token_ids}.")
        if any(
            any((not isinstance(token_id, int) or token_id < 0) for token_id in token_ids)
            for token_ids in nested_token_ids
        ):
            raise ValueError(
                f"Each list in `nested_token_ids` has to be a list of positive integers, but is {nested_token_ids}."
            )

        self.trie = DisjunctiveTrie(nested_token_ids)
        self.token_ids = nested_token_ids

        self.seqlen = self.trie.max_height
        self.current_seq = []
        self.completed = False

    def advance(self):
        token_list = self.trie.next_tokens(self.current_seq)

        if len(token_list) == 0:
            return None
        else:
            return token_list

    def does_advance(self, token_id: int):
        if not isinstance(token_id, int):
            raise TypeError(f"`token_id` is supposed to be type `int`, but is {token_id} of type {type(token_id)}")

        next_tokens = self.trie.next_tokens(self.current_seq)

        return token_id in next_tokens

    def update(self, token_id: int):
        if not isinstance(token_id, int):
            raise TypeError(f"`token_id` is supposed to be type `int`, but is {token_id} of type {type(token_id)}")

        stepped = False
        completed = False
        reset = False

        if self.does_advance(token_id):
            self.current_seq.append(token_id)
            stepped = True
        else:
            reset = True
            self.reset()

        completed = self.trie.reached_leaf(self.current_seq)
        self.completed = completed

        return stepped, completed, reset

    def reset(self):
        self.completed = False
        self.current_seq = []

    def remaining(self):
        if self.completed:
            # since this can be completed without reaching max height
            return 0
        else:
            return self.seqlen - len(self.current_seq)

    def copy(self, stateful=False):
        new_constraint = DisjunctiveConstraint(self.token_ids)

        if stateful:
            new_constraint.seq_len = self.seqlen
            new_constraint.current_seq = self.current_seq
            new_constraint.completed = self.completed

        return new_constraint

class ConstraintListState:
    r"""
    A class for beam scorers to track its progress through a list of constraints.

    Args:
        constraints (`List[Constraint]`):
            A list of [`Constraint`] objects that must be fulfilled by the beam scorer.
    """

    def __init__(self, constraints: List[Constraint]):
        self.constraints = constraints

        # max # of steps required to fulfill a given constraint
        self.max_seqlen = max([c.seqlen for c in constraints])
        self.n_constraints = len(constraints)
        self.completed = False

        self.init_state()

    def init_state(self):
        self.complete_constraints = []
        self.inprogress_constraint = None
        self.pending_constraints = [constraint.copy(stateful=False) for constraint in self.constraints]

    def get_bank(self):
        add = 0
        if self.inprogress_constraint:
            # extra points for having a constraint mid-fulfilled
            add += self.max_seqlen - self.inprogress_constraint.remaining()

        return (len(self.complete_constraints) * self.max_seqlen) + add

    def advance(self):
        """The list of tokens to generate such that we can make progress.
        By "list" we don't mean the list of token that will fully fulfill a constraint.

        Given constraints `c_i = {t_ij | j == # of tokens}`, If we're not in the middle of progressing through a
        specific constraint `c_i`, we return:

        `[t_k1 for k in indices of unfulfilled constraints]`

        If we are in the middle of a constraint, then we return:
            `[t_ij]`, where `i` is the index of the inprogress constraint, `j` is the next step for the constraint.

        Though we don't care which constraint is fulfilled first, if we are in the progress of fulfilling a constraint,
        that's the only one we'll return.
        """
        token_list = []
        if self.inprogress_constraint is None:
            for constraint in self.pending_constraints:  # "pending" == "unfulfilled yet"
                advance = constraint.advance()
                if isinstance(advance, int):
                    token_list.append(advance)
                elif isinstance(advance, list):
                    token_list.extend(advance)
        else:
            advance = self.inprogress_constraint.advance()
            if isinstance(advance, int):
                token_list.append(advance)
            elif isinstance(advance, list):
                token_list.extend(advance)

        if len(token_list) == 0:
            return None
        else:
            return token_list

    def reset(self, token_ids: Optional[List[int]]):
        """
        token_ids: the tokens generated thus far to reset the state of the progress through constraints.
        """
        self.init_state()

        if token_ids is not None:
            for token in token_ids:
                # completes or steps **one** constraint
                complete, stepped = self.add(token)

                # the entire list of constraints are fulfilled
                if self.completed:
                    break

    def add(self, token_id: int):
        if not isinstance(token_id, int):
            raise TypeError(f"`token_id` should be an `int`, but is `{token_id}`.")

        complete, stepped = False, False

        if self.completed:
            complete = True
            stepped = False
            return complete, stepped

        if self.inprogress_constraint is not None:
            # In the middle of fulfilling a constraint. If the `token_id` *does* makes an incremental progress to current
            # job, simply update the state

            stepped, complete, reset = self.inprogress_constraint.update(token_id)
            if reset:
                # 1. If the next token breaks the progress, then we must restart.
                #     e.g. constraint = "I love pies" and sequence so far is "I love" but `token_id` == "books".

                #     But that doesn't mean we self.init_state(), since we only reset the state for this particular
                #     constraint, not the full list of constraints.

                self.pending_constraints.append(self.inprogress_constraint.copy(stateful=False))
                self.inprogress_constraint = None

            if complete:
                # 2. If the next token completes the constraint, move it to completed list, set
                #     inprogress to None. If there are no pending constraints either, then this full list of constraints
                #     is complete.

                self.complete_constraints.append(self.inprogress_constraint)
                self.inprogress_constraint = None

                if len(self.pending_constraints) == 0:
                    # we're done!
                    self.completed = True

        else:
            # Not in the middle of fulfilling a constraint. So does this `token_id` helps us step towards any of our list
            # of constraints?

            for cidx, pending_constraint in enumerate(self.pending_constraints):
                if pending_constraint.does_advance(token_id):
                    stepped, complete, reset = pending_constraint.update(token_id)

                    if not stepped:
                        raise Exception(
                            "`constraint.update(token_id)` is not yielding incremental progress, "
                            "even though `constraint.does_advance(token_id)` is true."
                        )

                    if complete:
                        self.complete_constraints.append(pending_constraint)
                        self.inprogress_constraint = None

                    if not complete and stepped:
                        self.inprogress_constraint = pending_constraint

                    if complete or stepped:
                        # If we made any progress at all, then it's at least not a "pending constraint".

                        self.pending_constraints = (
                            self.pending_constraints[:cidx] + self.pending_constraints[cidx + 1 :]
                        )

                        if len(self.pending_constraints) == 0 and self.inprogress_constraint is None:
                            # If there's no longer any pending after this and no inprogress either, then we must be
                            # complete.

                            self.completed = True

                        break  # prevent accidentally stepping through multiple constraints with just one token.

        return complete, stepped

    def copy(self, stateful=True):
        new_state = ConstraintListState(self.constraints)  # we actually never though self.constraints objects
        # throughout this process. So it's at initialization state.

        if stateful:
            new_state.complete_constraints = [
                constraint.copy(stateful=True) for constraint in self.complete_constraints
            ]
            if self.inprogress_constraint is not None:
                new_state.inprogress_constraint = self.inprogress_constraint.copy(stateful=True)
            new_state.pending_constraints = [constraint.copy() for constraint in self.pending_constraints]

        return new_state


# ==================== generation/beam_search.py  (4.52 beam scorer 基类（5.x 重构移除）) ====================
from abc import ABC, abstractmethod
from collections import UserDict
from typing import Dict, List, Optional, Tuple, Union
import numpy as np
import torch



class BeamHypotheses:
    """
    Stores the hypotheses for beam search. Internal helper of [`BeamSearchScorer`] in 4.52.1
    (5.x 重写后不再导出，从 4.52.1 `generation/beam_search.py` 原样回填)。
    """

    def __init__(self, num_beams: int, length_penalty: float, early_stopping: bool, max_length: Optional[int] = None):
        """
        Initialize n-best list of hypotheses.
        """
        self.length_penalty = length_penalty
        self.early_stopping = early_stopping
        self.max_length = max_length
        self.num_beams = num_beams
        self.beams = []
        self.worst_score = 1e9

        if not isinstance(self.early_stopping, bool) and self.max_length is None:
            raise ValueError(
                "When `do_early_stopping` is set to a string, `max_length` must be defined. Ensure it is passed to the"
                " BeamScorer class instance at initialization time."
            )

    def __len__(self):
        """
        Number of hypotheses in the list.
        """
        return len(self.beams)

    def add(
        self,
        hyp: torch.LongTensor,
        sum_logprobs: float,
        beam_indices: Optional[torch.LongTensor] = None,
        generated_len: Optional[int] = None,
    ):
        """
        Add a new hypothesis to the list.
        """
        if generated_len is not None:
            score = sum_logprobs / (generated_len**self.length_penalty)
        # This 'else' case exists for retrocompatibility
        else:
            score = sum_logprobs / (hyp.shape[-1] ** self.length_penalty)

        if len(self) < self.num_beams or score > self.worst_score:
            self.beams.append((score, hyp, beam_indices))
            if len(self) > self.num_beams:
                sorted_next_scores = sorted([(s, idx) for idx, (s, _, _) in enumerate(self.beams)])
                del self.beams[sorted_next_scores[0][1]]
                self.worst_score = sorted_next_scores[1][0]
            else:
                self.worst_score = min(score, self.worst_score)

    def is_done(self, best_sum_logprobs: float, cur_len: int, decoder_prompt_len: Optional[int] = 0) -> bool:
        """
        If there are enough hypotheses and that none of the hypotheses being generated can become better than the worst
        one in the heap, then we are done with this sentence.
        """

        if len(self) < self.num_beams:
            return False

        # `True`: stop as soon as at least `num_beams` hypotheses are finished
        if self.early_stopping is True:
            return True
        # `False`: heuristic -- compute best possible score from `cur_len`, even though it is not entirely accurate
        #  when `length_penalty` is positive. See the discussion below for more details.
        # https://github.com/huggingface/transformers/pull/20901#issuecomment-1369845565
        elif self.early_stopping is False:
            highest_attainable_score = best_sum_logprobs / (cur_len - decoder_prompt_len) ** self.length_penalty
            ret = self.worst_score >= highest_attainable_score
            return ret
        # `"never"`: compute the best possible score, depending on the signal of `length_penalty`
        else:
            # `length_penalty` > 0.0 -> max denominator is obtaned from `max_length`, not from `cur_len` -> min
            # abs(`highest_attainable_score`) is obtained -> `highest_attainable_score` is negative, hence we obtain
            # its max this way
            if self.length_penalty > 0.0:
                if self.max_length <= decoder_prompt_len:
                    raise ValueError("max_length is not larger than decoder prompt length")
                highest_attainable_score = (
                    best_sum_logprobs / (self.max_length - decoder_prompt_len) ** self.length_penalty
                )
            # the opposite logic applies here (max `highest_attainable_score` from `cur_len`)
            else:
                highest_attainable_score = best_sum_logprobs / (cur_len - decoder_prompt_len) ** self.length_penalty
            ret = self.worst_score >= highest_attainable_score
            return ret


class BeamScorer(ABC):
    """
    Abstract base class for all beam scorers that are used for [`~PreTrainedModel.beam_search`] and
    [`~PreTrainedModel.beam_sample`].
    """

    @abstractmethod
    def process(
        self,
        input_ids: torch.LongTensor,
        next_scores: torch.FloatTensor,
        next_tokens: torch.LongTensor,
        next_indices: torch.LongTensor,
        **kwargs,
    ) -> Tuple[torch.Tensor]:
        raise NotImplementedError("This is an abstract method.")

    @abstractmethod
    def finalize(
        self,
        input_ids: torch.LongTensor,
        next_scores: torch.FloatTensor,
        next_tokens: torch.LongTensor,
        next_indices: torch.LongTensor,
        max_length: int,
        **kwargs,
    ) -> torch.LongTensor:
        raise NotImplementedError("This is an abstract method.")

class BeamSearchScorer(BeamScorer):
    r"""
    [`BeamScorer`] implementing standard beam search decoding.

    Adapted in part from [Facebook's XLM beam search
    code](https://github.com/facebookresearch/XLM/blob/9e6f6814d17be4fe5b15f2e6c43eb2b2d76daeb4/src/model/transformer.py#L529).

    Reference for the diverse beam search algorithm and implementation [Ashwin Kalyan's DBS
    implementation](https://github.com/ashwinkalyan/dbs/blob/master/dbs/beam_utils.lua)

    Args:
        batch_size (`int`):
            Batch Size of `input_ids` for which standard beam search decoding is run in parallel.
        num_beams (`int`):
            Number of beams for beam search.
        device (`torch.device`):
            Defines the device type (*e.g.*, `"cpu"` or `"cuda"`) on which this instance of `BeamSearchScorer` will be
            allocated.
        length_penalty (`float`, *optional*, defaults to 1.0):
            Exponential penalty to the length that is used with beam-based generation. It is applied as an exponent to
            the sequence length, which in turn is used to divide the score of the sequence. Since the score is the log
            likelihood of the sequence (i.e. negative), `length_penalty` > 0.0 promotes longer sequences, while
            `length_penalty` < 0.0 encourages shorter sequences.
        do_early_stopping (`bool` or `str`, *optional*, defaults to `False`):
            Controls the stopping condition for beam-based methods, like beam-search. It accepts the following values:
            `True`, where the generation stops as soon as there are `num_beams` complete candidates; `False`, where an
            heuristic is applied and the generation stops when is it very unlikely to find better candidates;
            `"never"`, where the beam search procedure only stops when there cannot be better candidates (canonical
            beam search algorithm).
        num_beam_hyps_to_keep (`int`, *optional*, defaults to 1):
            The number of beam hypotheses that shall be returned upon calling
            [`~transformers.BeamSearchScorer.finalize`].
        num_beam_groups (`int`, *optional*, defaults to 1):
            Number of groups to divide `num_beams` into in order to ensure diversity among different groups of beams.
            See [this paper](https://arxiv.org/pdf/1610.02424.pdf) for more details.
        max_length (`int`, *optional*):
            The maximum length of the sequence to be generated.
    """

    def __init__(
        self,
        batch_size: int,
        num_beams: int,
        device: torch.device,
        length_penalty: Optional[float] = 1.0,
        do_early_stopping: Optional[Union[bool, str]] = False,
        num_beam_hyps_to_keep: Optional[int] = 1,
        num_beam_groups: Optional[int] = 1,
        max_length: Optional[int] = None,
    ):
        self.num_beams = num_beams
        self.device = device
        self.length_penalty = length_penalty
        self.do_early_stopping = do_early_stopping
        self.num_beam_hyps_to_keep = num_beam_hyps_to_keep
        self.num_beam_groups = num_beam_groups
        self.group_size = self.num_beams // self.num_beam_groups

        self._is_init = False
        # self._beam_hyps[i*self.num_beam_groups+j] is the beam_hyps of the j-th group in the i-th mini-batch.
        # If group_beam_search is not used, the list consists of `batch_size` beam_hyps.
        self._beam_hyps = [
            BeamHypotheses(
                num_beams=self.group_size,
                length_penalty=self.length_penalty,
                early_stopping=self.do_early_stopping,
                max_length=max_length,
            )
            for _ in range(batch_size * self.num_beam_groups)
        ]
        # self._done[i*self.num_beam_groups+j] indicates whether the generation of the beam_hyps of the j-th group
        # in the i-th mini-batch is complete.
        self._done = torch.tensor(
            [False for _ in range(batch_size * self.num_beam_groups)], dtype=torch.bool, device=self.device
        )

        if not isinstance(num_beams, int) or num_beams <= 1:
            raise ValueError(
                f"`num_beams` has to be an integer strictly greater than 1, but is {num_beams}. For `num_beams` == 1,"
                " one should make use of `greedy_search` instead."
            )

        if not isinstance(num_beam_groups, int) or (num_beam_groups > num_beams) or (num_beams % num_beam_groups != 0):
            raise ValueError(
                "`num_beam_groups` has to be an integer smaller or equal than `num_beams` and `num_beams` has to be"
                f" divisible by `num_beam_groups`, but is {num_beam_groups} with `num_beams` being {num_beams}."
            )

    @property
    def is_done(self) -> bool:
        return self._done.all()

    def process(
        self,
        input_ids: torch.LongTensor,
        next_scores: torch.FloatTensor,
        next_tokens: torch.LongTensor,
        next_indices: torch.LongTensor,
        pad_token_id: Optional[Union[int, torch.Tensor]] = None,
        eos_token_id: Optional[Union[int, List[int], torch.Tensor]] = None,
        beam_indices: Optional[torch.LongTensor] = None,
        group_index: Optional[int] = 0,
        decoder_prompt_len: Optional[int] = 0,
    ) -> Dict[str, torch.Tensor]:
        # add up to the length which the next_scores is calculated on (including decoder prompt)
        cur_len = input_ids.shape[-1] + 1
        batch_size = len(self._beam_hyps) // self.num_beam_groups

        if not (batch_size == (input_ids.shape[0] // self.group_size)):
            if self.num_beam_groups > 1:
                raise ValueError(
                    f"A group beam size of {input_ids.shape[0]} is used as the input, but a group beam "
                    f"size of {self.group_size} is expected by the beam scorer."
                )
            else:
                raise ValueError(
                    f"A beam size of {input_ids.shape[0]} is used as the input, but a beam size of "
                    f"{self.group_size} is expected by the beam scorer."
                )

        device = input_ids.device
        next_beam_scores = torch.zeros((batch_size, self.group_size), dtype=next_scores.dtype, device=device)
        next_beam_tokens = torch.zeros((batch_size, self.group_size), dtype=next_tokens.dtype, device=device)
        next_beam_indices = torch.zeros((batch_size, self.group_size), dtype=next_indices.dtype, device=device)

        if eos_token_id is not None and not isinstance(eos_token_id, torch.Tensor):
            if isinstance(eos_token_id, int):
                eos_token_id = [eos_token_id]
            eos_token_id = torch.tensor(eos_token_id)

        for batch_idx in range(batch_size):
            batch_group_idx = batch_idx * self.num_beam_groups + group_index
            if self._done[batch_group_idx]:
                if self.num_beams < len(self._beam_hyps[batch_group_idx]):
                    raise ValueError(f"Batch can only be done if at least {self.num_beams} beams have been generated")
                if eos_token_id is None or pad_token_id is None:
                    raise ValueError("Generated beams >= num_beams -> eos_token_id and pad_token have to be defined")
                # pad the batch
                next_beam_scores[batch_idx, :] = 0
                next_beam_tokens[batch_idx, :] = pad_token_id
                next_beam_indices[batch_idx, :] = 0
                continue

            # next tokens for this sentence
            beam_idx = 0
            for beam_token_rank, (next_token, next_score, next_index) in enumerate(
                zip(next_tokens[batch_idx], next_scores[batch_idx], next_indices[batch_idx])
            ):
                batch_beam_idx = batch_idx * self.group_size + next_index
                # add to generated hypotheses if end of sentence
                if (eos_token_id is not None) and (next_token.item() in eos_token_id):
                    # if beam_token does not belong to top num_beams tokens, it should not be added
                    is_beam_token_worse_than_top_num_beams = beam_token_rank >= self.group_size
                    if is_beam_token_worse_than_top_num_beams:
                        continue
                    if beam_indices is not None:
                        beam_index = beam_indices[batch_beam_idx]
                        beam_index = beam_index + (batch_beam_idx,)
                    else:
                        beam_index = None

                    self._beam_hyps[batch_group_idx].add(
                        input_ids[batch_beam_idx].clone(),
                        next_score.item(),
                        beam_indices=beam_index,
                        generated_len=cur_len - decoder_prompt_len,
                    )
                else:
                    # add next predicted token since it is not eos_token
                    next_beam_scores[batch_idx, beam_idx] = next_score
                    next_beam_tokens[batch_idx, beam_idx] = next_token
                    next_beam_indices[batch_idx, beam_idx] = batch_beam_idx
                    beam_idx += 1

                # once the beam for next step is full, don't add more tokens to it.
                if beam_idx == self.group_size:
                    break

            if beam_idx < self.group_size:
                raise ValueError(
                    f"At most {self.group_size} tokens in {next_tokens[batch_idx]} can be equal to `eos_token_id:"
                    f" {eos_token_id}`. Make sure {next_tokens[batch_idx]} are corrected."
                )

            # Check if we are done so that we can save a pad step if all(done)
            self._done[batch_group_idx] = self._done[batch_group_idx] or self._beam_hyps[batch_group_idx].is_done(
                next_scores[batch_idx].max().item(), cur_len, decoder_prompt_len
            )

        return UserDict(
            {
                "next_beam_scores": next_beam_scores.view(-1),
                "next_beam_tokens": next_beam_tokens.view(-1),
                "next_beam_indices": next_beam_indices.view(-1),
            }
        )

    def finalize(
        self,
        input_ids: torch.LongTensor,
        final_beam_scores: torch.FloatTensor,
        final_beam_tokens: torch.LongTensor,
        final_beam_indices: torch.LongTensor,
        max_length: int,
        pad_token_id: Optional[Union[int, torch.Tensor]] = None,
        eos_token_id: Optional[Union[int, List[int], torch.Tensor]] = None,
        beam_indices: Optional[torch.LongTensor] = None,
        decoder_prompt_len: Optional[int] = 0,
    ) -> Tuple[torch.LongTensor]:
        batch_size = len(self._beam_hyps) // self.num_beam_groups

        if eos_token_id is not None and not isinstance(eos_token_id, torch.Tensor):
            if isinstance(eos_token_id, int):
                eos_token_id = [eos_token_id]
            eos_token_id = torch.tensor(eos_token_id)

        # finalize all open beam hypotheses and add to generated hypotheses
        for batch_group_idx, beam_hyp in enumerate(self._beam_hyps):
            if self._done[batch_group_idx]:
                continue

            # all open beam hypotheses are added to the beam hypothesis
            # beam hypothesis class automatically keeps the best beams
            for index_per_group in range(self.group_size):
                batch_beam_idx = batch_group_idx * self.group_size + index_per_group
                final_score = final_beam_scores[batch_beam_idx].item()
                final_tokens = input_ids[batch_beam_idx]
                beam_index = beam_indices[batch_beam_idx] if beam_indices is not None else None
                generated_len = final_tokens.shape[-1] - decoder_prompt_len
                beam_hyp.add(final_tokens, final_score, beam_indices=beam_index, generated_len=generated_len)

        # select the best hypotheses
        sent_lengths = input_ids.new(batch_size * self.num_beam_hyps_to_keep)
        best = []
        best_indices = []
        best_scores = torch.zeros(batch_size * self.num_beam_hyps_to_keep, device=self.device, dtype=torch.float32)

        # retrieve best hypotheses
        for i in range(batch_size):
            beam_hyps_in_batch = self._beam_hyps[i * self.num_beam_groups : (i + 1) * self.num_beam_groups]
            candidate_beams = [beam for beam_hyp in beam_hyps_in_batch for beam in beam_hyp.beams]
            sorted_hyps = sorted(candidate_beams, key=lambda x: x[0])
            for j in range(self.num_beam_hyps_to_keep):
                best_hyp_tuple = sorted_hyps.pop()
                best_score = best_hyp_tuple[0]
                best_hyp = best_hyp_tuple[1]
                best_index = best_hyp_tuple[2]
                sent_lengths[self.num_beam_hyps_to_keep * i + j] = len(best_hyp)

                # append hyp to lists
                best.append(best_hyp)

                # append indices to list
                best_indices.append(best_index)

                best_scores[i * self.num_beam_hyps_to_keep + j] = best_score

        # prepare for adding eos
        sent_lengths_max = sent_lengths.max().item() + 1
        sent_max_len = min(sent_lengths_max, max_length) if max_length is not None else sent_lengths_max
        decoded: torch.LongTensor = input_ids.new(batch_size * self.num_beam_hyps_to_keep, sent_max_len)

        if len(best_indices) > 0 and best_indices[0] is not None:
            indices: torch.LongTensor = input_ids.new(batch_size * self.num_beam_hyps_to_keep, sent_max_len)
        else:
            indices = None

        # shorter batches are padded if needed
        if sent_lengths.min().item() != sent_lengths.max().item():
            if pad_token_id is None:
                raise ValueError("`pad_token_id` has to be defined")
            decoded.fill_(pad_token_id)

        if indices is not None:
            indices.fill_(-1)

        # fill with hypotheses and eos_token_id if the latter fits in
        for i, (hypo, best_idx) in enumerate(zip(best, best_indices)):
            decoded[i, : sent_lengths[i]] = hypo

            if indices is not None:
                indices[i, : len(best_idx)] = torch.tensor(best_idx)

            if sent_lengths[i] < sent_max_len:
                # inserting only the first eos_token_id
                decoded[i, sent_lengths[i]] = eos_token_id[0]

        return UserDict(
            {
                "sequences": decoded,
                "sequence_scores": best_scores,
                "beam_indices": indices,
            }
        )

class ConstrainedBeamSearchScorer(BeamScorer):
    r"""
    [`BeamScorer`] implementing constrained beam search decoding.


    Args:
        batch_size (`int`):
            Batch Size of `input_ids` for which standard beam search decoding is run in parallel.
        num_beams (`int`):
            Number of beams for beam search.
        constraints (`List[Constraint]`):
            A list of positive constraints represented as `Constraint` objects that must be fulfilled in the generation
            output. For more information, the documentation of [`Constraint`] should be read.
        device (`torch.device`):
            Defines the device type (*e.g.*, `"cpu"` or `"cuda"`) on which this instance of `BeamSearchScorer` will be
            allocated.
        length_penalty (`float`, *optional*, defaults to 1.0):
            Exponential penalty to the length that is used with beam-based generation. It is applied as an exponent to
            the sequence length, which in turn is used to divide the score of the sequence. Since the score is the log
            likelihood of the sequence (i.e. negative), `length_penalty` > 0.0 promotes longer sequences, while
            `length_penalty` < 0.0 encourages shorter sequences.
        do_early_stopping (`bool` or `str`, *optional*, defaults to `False`):
            Controls the stopping condition for beam-based methods, like beam-search. It accepts the following values:
            `True`, where the generation stops as soon as there are `num_beams` complete candidates; `False`, where an
            heuristic is applied and the generation stops when is it very unlikely to find better candidates;
            `"never"`, where the beam search procedure only stops when there cannot be better candidates (canonical
            beam search algorithm).
        num_beam_hyps_to_keep (`int`, *optional*, defaults to 1):
            The number of beam hypotheses that shall be returned upon calling
            [`~transformers.BeamSearchScorer.finalize`].
        num_beam_groups (`int`, *optional*, defaults to 1):
            Number of groups to divide `num_beams` into in order to ensure diversity among different groups of beams.
            See [this paper](https://arxiv.org/pdf/1610.02424.pdf) for more details.
        max_length (`int`, *optional*):
            The maximum length of the sequence to be generated.
    """

    def __init__(
        self,
        batch_size: int,
        num_beams: int,
        constraints: List[Constraint],
        device: torch.device,
        length_penalty: Optional[float] = 1.0,
        do_early_stopping: Optional[Union[bool, str]] = False,
        num_beam_hyps_to_keep: Optional[int] = 1,
        num_beam_groups: Optional[int] = 1,
        max_length: Optional[int] = None,
    ):
        self.num_beams = num_beams
        self.device = device
        self.length_penalty = length_penalty
        self.do_early_stopping = do_early_stopping
        self.num_beam_hyps_to_keep = num_beam_hyps_to_keep
        self.num_beam_groups = num_beam_groups
        self.group_size = self.num_beams // self.num_beam_groups
        self.constraints = constraints

        self._is_init = False
        self._beam_hyps = [
            BeamHypotheses(
                num_beams=self.num_beams,
                length_penalty=self.length_penalty,
                early_stopping=self.do_early_stopping,
                max_length=max_length,
            )
            for _ in range(batch_size)
        ]
        self._done = torch.tensor([False for _ in range(batch_size)], dtype=torch.bool, device=self.device)

        if not isinstance(num_beams, int) or num_beams <= 1:
            raise ValueError(
                f"`num_beams` has to be an integer strictly greater than 1, but is {num_beams}. For `num_beams` == 1,"
                " one should make use of `greedy_search` instead."
            )

        if not isinstance(num_beam_groups, int) or (num_beam_groups > num_beams) or (num_beams % num_beam_groups != 0):
            raise ValueError(
                "`num_beam_groups` has to be an integer smaller or equal than `num_beams` and `num_beams` has to be"
                f" divisible by `num_beam_groups`, but is {num_beam_groups} with `num_beams` being {num_beams}."
            )

    @property
    def is_done(self) -> bool:
        return self._done.all()

    def make_constraint_states(self, n):
        return [ConstraintListState([constraint.copy() for constraint in self.constraints]) for _ in range(n)]

    def check_completes_constraints(self, sequence):
        new_state = self.make_constraint_states(1)[0]
        new_state.reset(sequence)
        return new_state.completed

    def process(
        self,
        input_ids: torch.LongTensor,
        next_scores: torch.FloatTensor,
        next_tokens: torch.LongTensor,
        next_indices: torch.LongTensor,
        scores_for_all_vocab: torch.FloatTensor,
        pad_token_id: Optional[Union[int, torch.Tensor]] = None,
        eos_token_id: Optional[Union[int, List[int], torch.Tensor]] = None,
        beam_indices: Optional[torch.LongTensor] = None,
        decoder_prompt_len: Optional[int] = 0,
    ) -> Tuple[torch.Tensor]:
        r"""
        Args:
            input_ids (`torch.LongTensor` of shape `(batch_size * num_beams, sequence_length)`):
                Indices of input sequence tokens in the vocabulary.

                Indices can be obtained using any class inheriting from [`PreTrainedTokenizer`]. See
                [`PreTrainedTokenizer.encode`] and [`PreTrainedTokenizer.__call__`] for details.

                [What are input IDs?](../glossary#input-ids)
            next_scores (`torch.FloatTensor` of shape `(batch_size, 2 * num_beams)`):
                Current scores of the top `2 * num_beams` non-finished beam hypotheses.
            next_tokens (`torch.LongTensor` of shape `(batch_size, 2 * num_beams)`):
                `input_ids` of the tokens corresponding to the top `2 * num_beams` non-finished beam hypotheses.
            next_indices (`torch.LongTensor` of shape `(batch_size, 2 * num_beams)`):
                Beam indices indicating to which beam hypothesis the `next_tokens` correspond.
            scores_for_all_vocab (`torch.FloatTensor` of shape `(batch_size * num_beams, sequence_length)`):
                The scores of all tokens in the vocabulary for each of the beam hypotheses.
            pad_token_id (`int`, *optional*):
                The id of the *padding* token.
            eos_token_id (`Union[int, List[int]]`, *optional*):
                The id of the *end-of-sequence* token. Optionally, use a list to set multiple *end-of-sequence* tokens.
            beam_indices (`torch.LongTensor`, *optional*):
                Beam indices indicating to which beam hypothesis each token correspond.
            decoder_prompt_len (`int`, *optional*):
                The length of prompt that is included in the input to decoder.
        Return:
            `UserDict`: A dictionary composed of the fields as defined above:

                - **next_beam_scores** (`torch.FloatTensor` of shape `(batch_size * num_beams)`) -- Updated scores of
                  all
                non-finished beams.

                - **next_beam_tokens** (`torch.FloatTensor` of shape `(batch_size * num_beams)`) -- Next tokens to be
                  added
                to the non-finished beam_hypotheses.
                - **next_beam_indices** (`torch.FloatTensor` of shape `(batch_size * num_beams)`) -- Beam indices
                indicating to which beam the next tokens shall be added.
        """

        # add up to the length which the next_scores is calculated on (including decoder prompt)
        cur_len = input_ids.shape[-1] + 1
        batch_size = len(self._beam_hyps)
        if not (batch_size == (input_ids.shape[0] // self.group_size)):
            if self.num_beam_groups > 1:
                raise ValueError(
                    f"A group beam size of {input_ids.shape[0]} is used as the input, but a group beam "
                    f"size of {self.group_size} is expected by the beam scorer."
                )
            else:
                raise ValueError(
                    f"A beam size of {input_ids.shape[0]} is used as the input, but a beam size of "
                    f"{self.group_size} is expected by the beam scorer."
                )

        device = input_ids.device

        next_beam_scores = torch.zeros((batch_size, self.group_size), dtype=next_scores.dtype, device=device)
        next_beam_tokens = torch.zeros((batch_size, self.group_size), dtype=next_tokens.dtype, device=device)
        next_beam_indices = torch.zeros((batch_size, self.group_size), dtype=next_indices.dtype, device=device)

        if eos_token_id is not None and not isinstance(eos_token_id, torch.Tensor):
            if isinstance(eos_token_id, int):
                eos_token_id = [eos_token_id]
            eos_token_id = torch.tensor(eos_token_id)

        for batch_idx, beam_hyp in enumerate(self._beam_hyps):
            if self._done[batch_idx]:
                if self.num_beams < len(beam_hyp):
                    raise ValueError(f"Batch can only be done if at least {self.num_beams} beams have been generated")
                if eos_token_id is None or pad_token_id is None:
                    raise ValueError("Generated beams >= num_beams -> eos_token_id and pad_token have to be defined")
                # pad the batch
                next_beam_scores[batch_idx, :] = 0
                next_beam_tokens[batch_idx, :] = pad_token_id
                next_beam_indices[batch_idx, :] = 0
                continue

            # next tokens for this sentence.
            beam_idx = 0
            for beam_token_rank, (next_token, next_score, next_index) in enumerate(
                zip(next_tokens[batch_idx], next_scores[batch_idx], next_indices[batch_idx])
            ):
                batch_beam_idx = batch_idx * self.group_size + next_index
                # add to generated hypotheses if end of sentence
                if (eos_token_id is not None) and (next_token.item() in eos_token_id):
                    # if beam_token does not belong to top num_beams tokens, it should not be added
                    is_beam_token_worse_than_top_num_beams = beam_token_rank >= self.group_size
                    if is_beam_token_worse_than_top_num_beams:
                        continue

                    completes_constraint = self.check_completes_constraints(input_ids[batch_beam_idx].tolist())
                    if completes_constraint:
                        if beam_indices is not None:
                            beam_index = beam_indices[batch_beam_idx]
                            beam_index = beam_index + (batch_beam_idx,)
                        else:
                            beam_index = None

                        beam_hyp.add(
                            input_ids[batch_beam_idx].clone(),
                            next_score.item(),
                            beam_indices=beam_index,
                            generated_len=cur_len - decoder_prompt_len,
                        )
                else:
                    # add next predicted token since it is not eos_token
                    next_beam_scores[batch_idx, beam_idx] = next_score
                    next_beam_tokens[batch_idx, beam_idx] = next_token
                    next_beam_indices[batch_idx, beam_idx] = batch_beam_idx
                    beam_idx += 1

                # once the beam for next step is full, don't add more tokens to it.
                if beam_idx == self.group_size:
                    break

            new_scores, new_tokens, new_indices = self.step_sentence_constraint(
                batch_idx,
                input_ids,
                scores_for_all_vocab,
                next_beam_scores[batch_idx],
                next_beam_tokens[batch_idx],
                next_beam_indices[batch_idx],
            )

            next_beam_scores[batch_idx] = new_scores
            next_beam_tokens[batch_idx] = new_tokens
            next_beam_indices[batch_idx] = new_indices

            if beam_idx < self.group_size:
                raise ValueError(
                    f"At most {self.group_size} tokens in {next_tokens[batch_idx]} can be equal to `eos_token_id:"
                    f" {eos_token_id}`. Make sure {next_tokens[batch_idx]} are corrected."
                )

            # Check if we are done so that we can save a pad step if all(done)
            self._done[batch_idx] = self._done[batch_idx] or beam_hyp.is_done(
                next_scores[batch_idx].max().item(), cur_len, decoder_prompt_len
            )

        return UserDict(
            {
                "next_beam_scores": next_beam_scores.view(-1),
                "next_beam_tokens": next_beam_tokens.view(-1),
                "next_beam_indices": next_beam_indices.view(-1),
            }
        )

    def step_sentence_constraint(
        self,
        batch_idx: int,
        input_ids: torch.LongTensor,
        vocab_scores: torch.FloatTensor,
        sent_beam_scores: torch.FloatTensor,
        sent_beam_tokens: torch.LongTensor,
        sent_beam_indices: torch.LongTensor,
        push_progress: bool = False,
    ):
        # sent_beam_tokens are the next {num_beams} number of tokens that are under consideration for this beam
        # (candidate next tokens)

        # 1. Adding "advance_tokens"
        #     using ConstraintStateList.advance(), we propose new tokens to be added into this "candidate list" that will
        #     advance us in fulfilling the constraints.

        # 2. Selecting best candidates such that we end up with highest probable candidates
        #     that fulfill our constraints.

        orig_len = sent_beam_indices.size(0)
        device = sent_beam_indices.device

        # initialize states
        topk_contraint_states = self.make_constraint_states(orig_len)
        advance_constraint_states = self.make_constraint_states(orig_len)

        sidx, eidx = batch_idx * orig_len, (batch_idx + 1) * orig_len
        this_batch_input_ids = input_ids[sidx:eidx]
        this_batch_token_scores = vocab_scores[sidx:eidx]
        full_hypotheses = torch.cat((input_ids[sent_beam_indices], sent_beam_tokens.unsqueeze(-1)), dim=-1)

        # need to make new hypothesis that advance the constraints
        track_new = {
            "new_seqs": full_hypotheses.tolist(),
            "new_states": [],
            "new_indices": [],
            "new_tokens": [],
            "new_scores": [],
        }
        for seq_idx, pre_seq in enumerate(this_batch_input_ids):
            # pre_seq = ith sequence generated before this step.

            # input_ids -> (topk) generic beam search best model next tokens
            #           -> (advance) constraints forcing the next token
            # either way, we need to sort them into "banks" later, so store a "ConstraintListState" for all types of
            # hypotheses.

            topk_state = topk_contraint_states[seq_idx]
            topk_state.reset(full_hypotheses[seq_idx].tolist())

            advance_state = advance_constraint_states[seq_idx]
            advance_state.reset(pre_seq.tolist())

            if not advance_state.completed:
                advance_tokens = torch.tensor(advance_state.advance(), dtype=torch.long, device=device)
                for advance_token in advance_tokens:
                    # since adding each `advance_token` leads to a different hypothesis, create new state instance.
                    new_state = advance_state.copy(stateful=True)
                    new_state.add(advance_token.tolist())

                    advance_seq = torch.cat((pre_seq, advance_token.unsqueeze(0)), -1).tolist()
                    if advance_seq not in track_new["new_seqs"]:
                        # prevent duplicates, which are basically bound to happen in this process.
                        track_new["new_seqs"].append(advance_seq)
                        track_new["new_indices"].append(sidx + seq_idx)  # idx -> global idx across all the batches
                        track_new["new_tokens"].append(advance_token)
                        track_new["new_scores"].append(this_batch_token_scores[seq_idx].take(advance_token))
                        track_new["new_states"].append(new_state)
            elif push_progress:
                # Basically, `sent_beam_indices` often chooses very little among `input_ids` the generated sequences that
                # actually fulfill our constraints. For example, let constraints == ["loves pies"] and

                #     pre_seq_1 = "The child loves pies and" pre_seq_2 = "The child plays in the playground and"

                # Without this step, if `sent_beam_indices` is something like [1,1], then
                #     1. `pre_seq_1` won't be added to the list of (topk) hypothesis since it's not in the indices and
                #     2.  it won't be added to the list of (advance) hypothesis since it's completed already. (this is
                #         the else part of `if constraints_completed[seq_idx]`)
                #     3. it ends up simply getting removed from consideration.

                # #3 might be fine and actually desired, since it's likely that it's a low-probability output anyways,
                # especially if it's not in the list of `sent_beam_indices`. But this often leads to lengthened beam
                # search times, since completed sequences keep getting removed after all this effort for constrained
                # generation.

                # Here, we basically take `pre_seq_1` and to "push" it into the considered list of hypotheses, by simply
                # appending the next likely token in the vocabulary and adding it to the list of hypotheses.

                new_score, new_token = torch.max(this_batch_token_scores[seq_idx], 0)  # some next probable token
                advance_seq = torch.cat((pre_seq, new_token.unsqueeze(0)), -1)

                advance_state = advance_constraint_states[seq_idx]

                advance_seq = advance_seq.tolist()

                advance_state.reset(advance_seq)
                if advance_seq not in track_new["new_seqs"]:
                    # but still don't want to have duplicates
                    track_new["new_seqs"].append(advance_seq)
                    track_new["new_indices"].append(seq_idx)
                    track_new["new_tokens"].append(new_token)
                    track_new["new_scores"].append(new_score)
                    track_new["new_states"].append(advance_state)

        if len(track_new["new_indices"]) > 0:
            new_indices = torch.tensor(track_new["new_indices"], device=device)
            new_tokens = torch.stack(track_new["new_tokens"]).to(device)
            new_scores = torch.stack(track_new["new_scores"]).to(device)

            all_states = topk_contraint_states + track_new["new_states"]
            all_tokens = torch.cat((sent_beam_tokens, new_tokens), -1)
            all_scores = torch.cat((sent_beam_scores, new_scores), -1)
            all_banks = torch.tensor([one.get_bank() for one in all_states], device=device)

            zipped = all_banks * 100 + all_scores
            indices = zipped.sort(descending=True).indices
            sorted_banks = all_banks[indices]

            # Then we end up with {sorted among bank C}, {sorted among bank C-1}, ..., {sorted among bank 0}

            counter = -1
            cur_bank = sorted_banks[0]
            increments = []
            for bank in sorted_banks:
                if bank == cur_bank:
                    counter += 1
                else:
                    counter = 0
                    cur_bank = bank
                increments.append(counter)
            rearrangers = torch.tensor(np.argsort(increments, kind="mergesort"))

            indices = indices[rearrangers][:orig_len]

            sent_beam_scores = all_scores[indices]
            sent_beam_tokens = all_tokens[indices]
            sent_beam_indices = torch.cat((sent_beam_indices, new_indices))[indices]

        return sent_beam_scores, sent_beam_tokens, sent_beam_indices

    def finalize(
        self,
        input_ids: torch.LongTensor,
        final_beam_scores: torch.FloatTensor,
        final_beam_tokens: torch.LongTensor,
        final_beam_indices: torch.LongTensor,
        max_length: int,
        pad_token_id: Optional[Union[int, torch.Tensor]] = None,
        eos_token_id: Optional[Union[int, List[int], torch.Tensor]] = None,
        beam_indices: Optional[torch.LongTensor] = None,
        decoder_prompt_len: Optional[int] = 0,
    ) -> Tuple[torch.LongTensor]:
        batch_size = len(self._beam_hyps)

        if eos_token_id is not None and not isinstance(eos_token_id, torch.Tensor):
            if isinstance(eos_token_id, int):
                eos_token_id = [eos_token_id]
            eos_token_id = torch.tensor(eos_token_id)

        # finalize all open beam hypotheses and add to generated hypotheses
        for batch_idx, beam_hyp in enumerate(self._beam_hyps):
            if self._done[batch_idx]:
                continue

            # all open beam hypotheses are added to the beam hypothesis
            # beam hypothesis class automatically keeps the best beams

            ids_collect = []
            for beam_id in range(self.num_beams):
                batch_beam_idx = batch_idx * self.num_beams + beam_id
                final_score = final_beam_scores[batch_beam_idx].item()
                final_tokens = input_ids[batch_beam_idx]

                completes_constraint = self.check_completes_constraints(final_tokens.tolist())
                if completes_constraint:
                    beam_index = beam_indices[batch_beam_idx] if beam_indices is not None else None
                    generated_len = final_tokens.shape[-1] - decoder_prompt_len
                    beam_hyp.add(final_tokens, final_score, beam_indices=beam_index, generated_len=generated_len)
                    ids_collect.append(beam_id)

            # due to overly complex constraints or other factors, sometimes we can't guarantee a successful
            # generation. In these cases we simply return the highest scoring outputs.
            if len(ids_collect) < self.num_beam_hyps_to_keep:
                for beam_id in range(self.num_beams):
                    if beam_id not in ids_collect:
                        batch_beam_idx = batch_idx * self.num_beams + beam_id
                        final_score = final_beam_scores[batch_beam_idx].item()
                        final_tokens = input_ids[batch_beam_idx]
                        generated_len = final_tokens.shape[-1] - decoder_prompt_len
                        beam_hyp.add(final_tokens, final_score, generated_len=generated_len)
                    if len(ids_collect) >= self.num_beam_hyps_to_keep:
                        break

        # select the best hypotheses
        sent_lengths = input_ids.new(batch_size * self.num_beam_hyps_to_keep)
        best = []
        best_indices = []
        best_scores = torch.zeros(batch_size * self.num_beam_hyps_to_keep, device=self.device, dtype=torch.float32)

        # retrieve best hypotheses
        for i, beam_hyp in enumerate(self._beam_hyps):
            sorted_hyps = sorted(beam_hyp.beams, key=lambda x: x[0])
            for j in range(self.num_beam_hyps_to_keep):
                best_hyp_tuple = sorted_hyps.pop()
                best_score = best_hyp_tuple[0]
                best_hyp = best_hyp_tuple[1]
                best_index = best_hyp_tuple[2]
                sent_lengths[self.num_beam_hyps_to_keep * i + j] = len(best_hyp)

                # append to lists
                best.append(best_hyp)

                # append indices to list
                best_indices.append(best_index)

                best_scores[i * self.num_beam_hyps_to_keep + j] = best_score

        # prepare for adding eos
        sent_lengths_max = sent_lengths.max().item() + 1

        sent_max_len = min(sent_lengths_max, max_length) if max_length is not None else sent_lengths_max
        decoded: torch.LongTensor = input_ids.new(batch_size * self.num_beam_hyps_to_keep, sent_max_len)

        if len(best_indices) > 0 and best_indices[0] is not None:
            indices: torch.LongTensor = input_ids.new(batch_size * self.num_beam_hyps_to_keep, sent_max_len)
        else:
            indices = None

        # shorter batches are padded if needed
        if sent_lengths.min().item() != sent_lengths.max().item():
            if pad_token_id is None:
                raise ValueError("`pad_token_id` has to be defined")
            decoded.fill_(pad_token_id)

        if indices is not None:
            indices.fill_(-1)

        # fill with hypotheses and eos_token_id if the latter fits in
        for i, (hypo, best_idx) in enumerate(zip(best, best_indices)):
            decoded[i, : sent_lengths[i]] = hypo

            if indices is not None:
                indices[i, : len(best_idx)] = torch.tensor(best_idx)

            if sent_lengths[i] < sent_max_len:
                # inserting only the first eos_token_id
                decoded[i, sent_lengths[i]] = eos_token_id[0]

        return UserDict(
            {
                "sequences": decoded,
                "sequence_scores": best_scores,
                "beam_indices": indices,
            }
        )


# ==================== generation/logits_process.py  (4.52 HammingDiversity（5.x 移除）) ====================
import inspect
import math
from typing import Callable, Iterable, List, Optional, Tuple, Union
import numpy as np
import torch

from transformers.utils.logging import get_logger
from transformers.generation.logits_process import LogitsProcessor  # 5.20.2 官方仍提供基类

class HammingDiversityLogitsProcessor(LogitsProcessor):
    r"""
    [`LogitsProcessor`] that enforces diverse beam search.

    Note that this logits processor is only effective for [`PreTrainedModel.group_beam_search`]. See [Diverse Beam
    Search: Decoding Diverse Solutions from Neural Sequence Models](https://arxiv.org/pdf/1610.02424.pdf) for more
    details.

    Traditional beam search often generates very similar sequences across different beams.
    `HammingDiversityLogitsProcessor` addresses this by penalizing beams that generate tokens already chosen by other
    beams in the same time step.

    Args:
        diversity_penalty (`float`):
            This value is subtracted from a beam's score if it generates a token same as any beam from other group at a
            particular time. A higher `diversity_penalty` will enforce greater diversity among the beams. Adjusting
            this value can help strike a balance between diversity and natural likelihood.
        num_beams (`int`):
            Number of beams for beam search. 1 means no beam search.
        num_beam_groups (`int`):
            Number of groups to divide `num_beams` into in order to ensure diversity among different groups of beams.
            [this paper](https://arxiv.org/pdf/1610.02424.pdf) for more details.

    Examples:

    ```python
    >>> from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
    >>> import torch

    >>> # Initialize the model and tokenizer
    >>> tokenizer = AutoTokenizer.from_pretrained("google-t5/t5-base")
    >>> model = AutoModelForSeq2SeqLM.from_pretrained("google-t5/t5-base")

    >>> # A long text about the solar system
    >>> text = (
    ...     "The Solar System is a gravitationally bound system comprising the Sun and the objects that orbit it, "
    ...     "either directly or indirectly. Of the objects that orbit the Sun directly, the largest are the eight "
    ...     "planets, with the remainder being smaller objects, such as the five dwarf planets and small Solar System "
    ...     "bodies. The Solar System formed 4.6 billion years ago from the gravitational collapse of a giant "
    ...     "interstellar molecular cloud."
    ... )
    >>> inputs = tokenizer("summarize: " + text, return_tensors="pt")

    >>> # Generate diverse summary
    >>> outputs_diverse = model.generate(
    ...     **inputs,
    ...     num_beam_groups=2,
    ...     diversity_penalty=10.0,
    ...     max_length=100,
    ...     num_beams=4,
    ...     num_return_sequences=2,
    ... )
    >>> summaries_diverse = tokenizer.batch_decode(outputs_diverse, skip_special_tokens=True)

    >>> # Generate non-diverse summary
    >>> outputs_non_diverse = model.generate(
    ...     **inputs,
    ...     max_length=100,
    ...     num_beams=4,
    ...     num_return_sequences=2,
    ... )
    >>> summary_non_diverse = tokenizer.batch_decode(outputs_non_diverse, skip_special_tokens=True)

    >>> # With `diversity_penalty`, the resulting beams are much more diverse
    >>> print(summary_non_diverse)
    ['the solar system formed 4.6 billion years ago from the collapse of a giant interstellar molecular cloud. of the objects that orbit the Sun directly, the largest are the eight planets.',
    'the Solar System formed 4.6 billion years ago from the collapse of a giant interstellar molecular cloud. of the objects that orbit the Sun directly, the largest are the eight planets.']

    >>> print(summaries_diverse)
    ['the solar system formed 4.6 billion years ago from the collapse of a giant interstellar molecular cloud. of the objects that orbit the Sun directly, the largest are the eight planets.',
    'the solar system formed 4.6 billion years ago from the collapse of a giant interstellar molecular cloud. of the objects that orbit the Sun directly, the largest are the eight planets. the rest of the objects are smaller objects, such as the five dwarf planets and small solar system bodies.']
    ```
    """

    def __init__(self, diversity_penalty: float, num_beams: int, num_beam_groups: int):
        if not isinstance(diversity_penalty, float) or (not diversity_penalty > 0.0):
            raise ValueError("`diversity_penalty` should be a float strictly larger than 0.")
        self._diversity_penalty = diversity_penalty
        if not isinstance(num_beams, int) or num_beams < 2:
            raise ValueError("`num_beams` should be an integer strictly larger than 1.")
        self._num_beams = num_beams
        if not isinstance(num_beam_groups, int) or num_beam_groups < 2:
            raise ValueError("`num_beam_groups` should be an integer strictly larger than 1.")
        if num_beam_groups > num_beams:
            raise ValueError("`beam_groups` has to be smaller or equal to `num_beams`.")
        self._num_sub_beams = num_beams // num_beam_groups

    def __call__(
        self,
        input_ids: torch.LongTensor,
        scores: torch.FloatTensor,
        current_tokens: torch.LongTensor,
        beam_group_idx: int,
    ) -> torch.FloatTensor:
        r"""
        Args:
            input_ids (`torch.LongTensor` of shape `(batch_size, sequence_length)`):
                Indices of input sequence tokens in the vocabulary. [What are input IDs?](../glossary#input-ids)
            scores (`torch.FloatTensor` of shape `(batch_size, config.vocab_size)`):
                Prediction scores of a language modeling head. These can be logits for each vocabulary when not using
                beam search or log softmax for each vocabulary token when using beam search
            current_tokens (`torch.LongTensor` of shape `(batch_size)`):
                Indices of input sequence tokens in the vocabulary, corresponding to the tokens selected by the other
                beam groups in the current generation step.
            beam_group_idx (`int`):
                The index of the beam group currently being processed.

        Return:
            `torch.FloatTensor` of shape `(batch_size, config.vocab_size)`:
                The processed prediction scores.
        """
        # hamming diversity: penalise using same token in current group which was used in previous groups at
        # the same time step
        batch_size = current_tokens.shape[0] // self._num_beams
        group_start_idx = beam_group_idx * self._num_sub_beams
        group_end_idx = min(group_start_idx + self._num_sub_beams, self._num_beams)
        group_size = group_end_idx - group_start_idx
        vocab_size = scores.shape[-1]

        if group_start_idx == 0:
            return scores

        scores_processed = scores.clone()
        for batch_idx in range(batch_size):
            # predicted tokens of last time step of previous groups
            previous_group_tokens = current_tokens[
                batch_idx * self._num_beams : batch_idx * self._num_beams + group_start_idx
            ]
            token_frequency = torch.bincount(previous_group_tokens, minlength=vocab_size).to(scores.device)
            scores_processed[batch_idx * group_size : (batch_idx + 1) * group_size] -= (
                self._diversity_penalty * token_frequency
            )

        return scores_processed


# ==================== utils/hub.py  (4.52 download_url / is_offline_mode / is_remote_url（5.x 移除/搬家）) ====================
import json
import os
import re
import sys
import tempfile
import warnings
from concurrent import futures
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlparse
from uuid import uuid4
import huggingface_hub
import requests
from huggingface_hub import (
    _CACHED_NO_EXIST,
    CommitOperationAdd,
    ModelCard,
    ModelCardData,
    constants,
    create_branch,
    create_commit,
    create_repo,
    hf_hub_download,
    hf_hub_url,
    list_repo_tree,
    snapshot_download,
    try_to_load_from_cache,
)


def is_offline_mode():
    import huggingface_hub.constants as _hfc

    return _hfc.HF_HUB_OFFLINE


def is_remote_url(url_or_filename):
    parsed = urlparse(url_or_filename)
    return parsed.scheme in ("http", "https")

def download_url(url, proxies=None):
    """
    Downloads a given url in a temporary file. This function is not safe to use in multiple processes. Its only use is
    for deprecated behavior allowing to download config/models with a single url instead of using the Hub.

    Args:
        url (`str`): The url of the file to download.
        proxies (`Dict[str, str]`, *optional*):
            A dictionary of proxy servers to use by protocol or endpoint, e.g., `{'http': 'foo.bar:3128',
            'http://hostname': 'foo.bar:4012'}.` The proxies are used on each request.

    Returns:
        `str`: The location of the temporary file where the url was downloaded.
    """
    warnings.warn(
        f"Using `from_pretrained` with the url of a file (here {url}) is deprecated and won't be possible anymore in"
        " v5 of Transformers. You should host your file on the Hub (hf.co) instead and use the repository ID. Note"
        " that this is not compatible with the caching system (your file will be downloaded at each execution) or"
        " multiple processes (each process will download the file in a different temporary file).",
        FutureWarning,
    )
    from huggingface_hub.file_download import http_get

    tmp_fd, tmp_file = tempfile.mkstemp()
    with os.fdopen(tmp_fd, "wb") as f:
        http_get(url, f, proxies=proxies)
    return tmp_file


# ==================== utils/import_utils.py  (4.52 能力探测函数（5.x 移除；indextts 从 transformers.utils 顶层导入）) ====================
import importlib.machinery
import importlib.metadata
import importlib.util
import json
import operator
import os
import re
import shutil
import subprocess
import sys
import warnings
from collections import OrderedDict
from enum import Enum
from functools import lru_cache
from itertools import chain
from types import ModuleType
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple, Union
from packaging import version
from transformers.utils import logging
import importlib.util as _ilu  # 函数体依赖的模块级布尔（4.52 import_utils L194）
_safetensors_available = _ilu.find_spec('safetensors') is not None
from transformers.utils.import_utils import is_torch_available, _is_package_available  # 5.20.2 仍在 import_utils 子模块
_torch_available, _torch_version = _is_package_available('torch', return_version=True)  # 4.52 import_utils L232

def is_torch_sdpa_available():
    if not is_torch_available():
        return False
    elif _torch_version == "N/A":
        return False

    # NOTE: MLU is OK with non-contiguous inputs.
    if is_torch_mlu_available():
        return True
    # NOTE: NPU can use SDPA in Transformers with torch>=2.1.0.
    if is_torch_npu_available():
        return True
    # NOTE: We require torch>=2.1.1 to avoid a numerical issue in SDPA with non-contiguous inputs: https://github.com/pytorch/pytorch/issues/112577
    return version.parse(_torch_version) >= version.parse("2.1.1")

@lru_cache()
def is_torch_npu_available(check_device=False):
    "Checks if `torch_npu` is installed and potentially if a NPU is in the environment"
    if not _torch_available or importlib.util.find_spec("torch_npu") is None:
        return False

    import torch
    import torch_npu  # noqa: F401

    if check_device:
        try:
            # Will raise a RuntimeError if no NPU is found
            _ = torch.npu.device_count()
            return torch.npu.is_available()
        except RuntimeError:
            return False
    return hasattr(torch, "npu") and torch.npu.is_available()

@lru_cache()
def is_torch_mlu_available(check_device=False):
    """
    Checks if `mlu` is available via an `cndev-based` check which won't trigger the drivers and leave mlu
    uninitialized.
    """
    if not _torch_available or importlib.util.find_spec("torch_mlu") is None:
        return False

    import torch
    import torch_mlu  # noqa: F401

    pytorch_cndev_based_mlu_check_previous_value = os.environ.get("PYTORCH_CNDEV_BASED_MLU_CHECK")
    try:
        os.environ["PYTORCH_CNDEV_BASED_MLU_CHECK"] = str(1)
        available = torch.mlu.is_available()
    finally:
        if pytorch_cndev_based_mlu_check_previous_value:
            os.environ["PYTORCH_CNDEV_BASED_MLU_CHECK"] = pytorch_cndev_based_mlu_check_previous_value
        else:
            os.environ.pop("PYTORCH_CNDEV_BASED_MLU_CHECK", None)

    return available

def is_safetensors_available():
    return _safetensors_available


# ==================== utils/model_parallel_utils.py  (4.52 model_parallel 工具（5.x 整模块移除）) ====================
from math import ceil


def assert_device_map(device_map, num_blocks):
    blocks = list(range(0, num_blocks))

    device_map_blocks = [item for sublist in list(device_map.values()) for item in sublist]

    # Duplicate check
    duplicate_blocks = []
    for i in device_map_blocks:
        if device_map_blocks.count(i) > 1 and i not in duplicate_blocks:
            duplicate_blocks.append(i)
    # Missing blocks
    missing_blocks = [i for i in blocks if i not in device_map_blocks]
    extra_blocks = [i for i in device_map_blocks if i not in blocks]

    if len(duplicate_blocks) != 0:
        raise ValueError(
            "Duplicate attention blocks specified in device_map. Attention blocks must be specified to one device."
            " These attention blocks were specified more than once: " + str(duplicate_blocks)
        )
    if len(missing_blocks) != 0:
        raise ValueError(
            "There are attention blocks for this model that are not specified in the device_map. Add these attention "
            "blocks to a device on the device_map: " + str(missing_blocks)
        )
    if len(extra_blocks) != 0:
        raise ValueError(
            "The device_map contains more attention blocks than this model has. Remove these from the device_map:"
            + str(extra_blocks)
        )

def get_device_map(n_layers, devices):
    """Returns a dictionary of layers distributed evenly across all devices."""
    layers = list(range(n_layers))
    n_blocks = int(ceil(n_layers / len(devices)))
    layers_list = [layers[i : i + n_blocks] for i in range(0, n_layers, n_blocks)]

    return dict(zip(devices, layers_list))


# ==================== dynamic_module_utils.py  (4.52 custom_object_save（5.x 移除）) ====================
import ast
import filecmp
import hashlib
import importlib
import importlib.metadata
import importlib.util
import os
import re
import shutil
import signal
import sys
import threading
import warnings
from pathlib import Path
from types import ModuleType
from typing import Any, Optional, Union
from huggingface_hub import try_to_load_from_cache
from packaging import version
from transformers.utils import (
    HF_MODULES_CACHE,
    TRANSFORMERS_DYNAMIC_MODULE_NAME,
    cached_file,
    extract_commit_hash,
    is_offline_mode,
    logging,
)


def get_relative_imports(module_file: Union[str, os.PathLike]) -> list[str]:
    """4.52.1 `dynamic_module_utils.get_relative_imports` 原样回填。"""
    import re

    with open(module_file, encoding="utf-8") as f:
        content = f.read()

    relative_imports = re.findall(r"^\s*import\s+\.(\S+)\s*$", content, flags=re.MULTILINE)
    relative_imports += re.findall(r"^\s*from\s+\.(\S+)\s+import", content, flags=re.MULTILINE)
    return list(set(relative_imports))


def get_relative_import_files(module_file: Union[str, os.PathLike]) -> list[str]:
    """4.52.1 `dynamic_module_utils.get_relative_import_files` 原样回填（递归收集相对导入）。"""
    from pathlib import Path

    no_change = False
    files_to_check = [module_file]
    all_relative_imports = []

    while not no_change:
        new_imports = []
        for f in files_to_check:
            new_imports.extend(get_relative_imports(f))

        module_path = Path(module_file).parent
        new_import_files = [str(module_path / m) for m in new_imports]
        new_import_files = [f for f in new_import_files if f not in all_relative_imports]
        files_to_check = [f"{f}.py" for f in new_import_files]

        no_change = len(new_import_files) == 0
        all_relative_imports.extend(files_to_check)

    return all_relative_imports


def custom_object_save(obj: Any, folder: Union[str, os.PathLike], config: Optional[dict] = None) -> list[str]:
    """
    Save the modeling files corresponding to a custom model/configuration/tokenizer etc. in a given folder. Optionally
    adds the proper fields in a config.

    Args:
        obj (`Any`): The object for which to save the module files.
        folder (`str` or `os.PathLike`): The folder where to save.
        config (`PretrainedConfig` or dictionary, `optional`):
            A config in which to register the auto_map corresponding to this custom object.

    Returns:
        `List[str]`: The list of files saved.
    """
    if obj.__module__ == "__main__":
        logger.warning(
            f"We can't save the code defining {obj} in {folder} as it's been defined in __main__. You should put "
            "this code in a separate module so we can include it in the saved folder and make it easier to share via "
            "the Hub."
        )
        return

    def _set_auto_map_in_config(_config):
        module_name = obj.__class__.__module__
        last_module = module_name.split(".")[-1]
        full_name = f"{last_module}.{obj.__class__.__name__}"
        # Special handling for tokenizers
        if "Tokenizer" in full_name:
            slow_tokenizer_class = None
            fast_tokenizer_class = None
            if obj.__class__.__name__.endswith("Fast"):
                # Fast tokenizer: we have the fast tokenizer class and we may have the slow one has an attribute.
                fast_tokenizer_class = f"{last_module}.{obj.__class__.__name__}"
                if getattr(obj, "slow_tokenizer_class", None) is not None:
                    slow_tokenizer = getattr(obj, "slow_tokenizer_class")
                    slow_tok_module_name = slow_tokenizer.__module__
                    last_slow_tok_module = slow_tok_module_name.split(".")[-1]
                    slow_tokenizer_class = f"{last_slow_tok_module}.{slow_tokenizer.__name__}"
            else:
                # Slow tokenizer: no way to have the fast class
                slow_tokenizer_class = f"{last_module}.{obj.__class__.__name__}"

            full_name = (slow_tokenizer_class, fast_tokenizer_class)

        if isinstance(_config, dict):
            auto_map = _config.get("auto_map", {})
            auto_map[obj._auto_class] = full_name
            _config["auto_map"] = auto_map
        elif getattr(_config, "auto_map", None) is not None:
            _config.auto_map[obj._auto_class] = full_name
        else:
            _config.auto_map = {obj._auto_class: full_name}

    # Add object class to the config auto_map
    if isinstance(config, (list, tuple)):
        for cfg in config:
            _set_auto_map_in_config(cfg)
    elif config is not None:
        _set_auto_map_in_config(config)

    result = []
    # Copy module file to the output folder.
    object_file = sys.modules[obj.__module__].__file__
    dest_file = Path(folder) / (Path(object_file).name)
    shutil.copy(object_file, dest_file)
    result.append(dest_file)

    # Gather all relative imports recursively and make sure they are copied as well.
    for needed_file in get_relative_import_files(object_file):
        dest_file = Path(folder) / (Path(needed_file).name)
        shutil.copy(needed_file, dest_file)
        result.append(dest_file)

    return result


# ==================== tokenization_utils.py  (4.52 Trie 前缀树体系（5.x 整体移除，tokenization_utils 模块已不存在）) ====================
import bisect
import itertools
import re
import unicodedata
from collections import OrderedDict
from typing import Any, Optional, Union, overload
from transformers.tokenization_utils_base import (
    ENCODE_KWARGS_DOCSTRING,
    ENCODE_PLUS_ADDITIONAL_KWARGS_DOCSTRING,
    INIT_TOKENIZER_DOCSTRING,
    AddedToken,
    BatchEncoding,
    EncodedInput,
    EncodedInputPair,
    PreTokenizedInput,
    PreTokenizedInputPair,
    PreTrainedTokenizerBase,
    TextInput,
    TextInputPair,
    TruncationStrategy,
)


class Trie:
    """
    Trie in Python. Creates a Trie out of a list of words. The trie is used to split on `added_tokens` in one pass
    Loose reference https://en.wikipedia.org/wiki/Trie
    """

    def __init__(self, *args):
        self.data = {}
        self._tokens = set()
        self._termination_char = ""
        self.update(*args)

    def update(self, *args):
        """
        Updates the Trie with new tokens provided as arguments.

        Args:
            *args: Variable number of words to be added to the Trie.
        """
        for token in tuple(*args):
            self.add(token)

    def add(self, word: str):
        """
        Passes over every char (utf-8 char) on word and recursively adds it to the internal `data` trie representation.
        The special key `""` in `self._termination_char` is used to represent termination.

        This function is idempotent, adding twice the same word will leave the trie unchanged

        Example:

        ```python
        >>> trie = Trie()
        >>> trie.add("Hello 友達")
        >>> trie.data
        {"H": {"e": {"l": {"l": {"o": {" ": {"友": {"達": {"": 1}}}}}}}}}

        >>> trie.add("Hello")
        >>> trie.data
        {"H": {"e": {"l": {"l": {"o": {"": 1, " ": {"友": {"達": {"": 1}}}}}}}}}
        ```
        """
        if not word:
            # Prevent empty string
            return

        self._tokens.add(word)
        ref = self.data
        for char in word:
            ref[char] = ref.setdefault(char, {})
            ref = ref[char]
        ref[self._termination_char] = 1

    def split(self, text: str) -> list[str]:
        """
        Will look for the words added to the trie within `text`. Output is the original string splitted along the
        boundaries of the words found.

        This trie will match the longest possible word first !

        Example:

        ```python
        >>> trie = Trie()
        >>> trie.split("[CLS] This is a extra_id_100")
        ["[CLS] This is a extra_id_100"]

        >>> trie.add("[CLS]")
        >>> trie.add("extra_id_1")
        >>> trie.add("extra_id_100")
        >>> trie.split("[CLS] This is a extra_id_100")
        ["[CLS]", " This is a ", "extra_id_100"]
        ```
        """
        # indexes are counted left of the chars index.
        # "hello", index 0, is left of h, index 1 is between h and e.
        # index 5 is right of the "o".

        # States are going to capture every possible start (indexes as above)
        # as keys, and have as values, a pointer to the position in the trie
        # where we're at. This is a partial match for now.
        # This enables to keep track of multiple matches while we're iterating
        # the string
        # If the trie contains, "blowing", and "lower" and we encounter the
        # string "blower", we need to split into ["b", "lower"].
        # This is where we need to keep track of multiple possible starts.
        states = OrderedDict()

        # This will contain every indices where we need
        # to cut.
        # We force to cut at offset 0 and len(text) (added later)
        offsets = [0]

        # This is used by the lookahead which needs to skip over
        # some text where the full match exceeded the place in the initial
        # for loop
        skip = 0
        # Main loop, Giving this algorithm O(n) complexity
        for current, current_char in enumerate(text):
            if skip and current < skip:
                # Prevents the lookahead for matching twice
                # like extra_id_100 and id_100
                continue

            # This will track every state
            # that stop matching, we need to stop tracking them.
            # If we look at "lowball", we're going to match "l" (add it to states), "o", "w", then
            # fail on "b", we need to remove 0 from the valid states.
            to_remove = set()
            # Whenever we found a match, we need to drop everything
            # this is a greedy algorithm, it will match on the first found token
            reset = False

            # In this case, we already have partial matches (But unfinished)
            for start, trie_pointer in states.items():
                if "" in trie_pointer:
                    # This is a final match, we need to reset and
                    # store the results in `offsets`.

                    # Lookahead to match longest first
                    # Important in case of extra_id_1 vs extra_id_100
                    # Here we are also actively looking for other earlier partial
                    # matches
                    # "[CLS]", "L", we need to match CLS even if L is special
                    for lookstart, looktrie_pointer in states.items():
                        if lookstart > start:
                            # This partial match is later, we can stop looking
                            break
                        elif lookstart < start:
                            # This partial match is earlier, the trie pointer
                            # was already updated, so index is + 1
                            lookahead_index = current + 1
                            end = current + 1
                        else:
                            # Here lookstart == start and
                            #      looktrie_pointer == trie_pointer
                            # It wasn't updated yet so indices are current ones
                            lookahead_index = current
                            end = current
                        next_char = text[lookahead_index] if lookahead_index < len(text) else None
                        if "" in looktrie_pointer:
                            start = lookstart
                            end = lookahead_index
                            skip = lookahead_index

                        while next_char in looktrie_pointer:
                            looktrie_pointer = looktrie_pointer[next_char]
                            lookahead_index += 1
                            if "" in looktrie_pointer:
                                start = lookstart
                                end = lookahead_index
                                skip = lookahead_index

                            if lookahead_index == len(text):
                                # End of string
                                break
                            next_char = text[lookahead_index]
                        # End lookahead

                    # Storing and resetting
                    offsets.append(start)
                    offsets.append(end)
                    reset = True
                    break
                elif current_char in trie_pointer:
                    # The current character being looked at has a match within the trie
                    # update the pointer (it will be stored back into states later).
                    trie_pointer = trie_pointer[current_char]

                    # Storing back the new pointer into the states.
                    # Partial matches got longer by one.
                    states[start] = trie_pointer
                else:
                    # The new character has not match in the trie, we need
                    # to stop keeping track of this partial match.
                    # We can't do it directly within the loop because of how
                    # python iteration works
                    to_remove.add(start)

            # Either clearing the full start (we found a real match)
            # Or clearing only the partial matches that didn't work.
            if reset:
                states = {}
            else:
                for start in to_remove:
                    del states[start]

            # If this character is a starting character within the trie
            # start keeping track of this partial match.
            if current >= skip and current_char in self.data:
                states[current] = self.data[current_char]

        # We have a cut at the end with states.
        for start, trie_pointer in states.items():
            if "" in trie_pointer:
                # This is a final match, we need to reset and
                # store the results in `offsets`.
                end = len(text)
                offsets.append(start)
                offsets.append(end)
                # Longest cut is always the one with lower start so the first
                # item so we need to break.
                break

        return self.cut_text(text, offsets)

    def cut_text(self, text, offsets):
        # We have all the offsets now, we just need to do the actual splitting.
        # We need to eventually add the first part of the string and the eventual
        # last part.
        offsets.append(len(text))
        tokens = []
        start = 0
        for end in offsets:
            if start > end:
                logger.error(
                    "There was a bug in Trie algorithm in tokenization. Attempting to recover. Please report it"
                    " anyway."
                )
                continue
            elif start == end:
                # This might happen if there's a match at index 0
                # we're also preventing zero-width cuts in case of two
                # consecutive matches
                continue
            tokens.append(text[start:end])
            start = end

        return tokens

class ExtensionsTrie(Trie):
    def __init__(self, *args):
        super().__init__(*args)

    def extensions(self, prefix: str):
        """
        Generates all extensions of a given prefix token in the Trie.

        Example:

        ```python
        >>> trie = Trie()
        >>> trie.add("apple")
        >>> trie.add("app")
        >>> trie.add("application")
        >>> trie.extensions("app")
        ['app', 'apple', 'application']
        ```
        """
        prefix_node = self._get_node(prefix)
        ret = self._collect_tokens(prefix_node)
        return [prefix + token for token in ret]

    def _get_node(self, token: str) -> dict:
        """
        Retrieves the node corresponding to the given token in the Trie.

        Args:
            token (str): The token for which the corresponding node needs to be retrieved.

        Returns:
            dict: The node in the Trie corresponding to the given token.
        """
        node = self.data
        for char in token:
            if char not in node:
                break

            node = node[char]
        return node

    def _collect_tokens(self, node: dict) -> list:
        """
        Generates all tokens in the Trie starting from a given node.

        Args:
            node (dict): The node in the Trie from which tokens need to be generated.

        Returns:
            list: List of tokens generated from the given node.
        """
        tokens = [self._termination_char] if self._termination_char in node else []
        for token, subtrie_head in node.items():
            if token != self._termination_char:
                subtokens = self._collect_tokens(subtrie_head)
                tokens.extend([token + subtoken for subtoken in subtokens])
        return tokens


def _install_all():
    _m_transformers_pytorch_utils = _ensure_module("transformers.pytorch_utils")
    if not hasattr(_m_transformers_pytorch_utils, "prune_linear_layer"):
        setattr(_m_transformers_pytorch_utils, "prune_linear_layer", prune_linear_layer)
    if not hasattr(_m_transformers_pytorch_utils, "Conv1D"):
        setattr(_m_transformers_pytorch_utils, "Conv1D", Conv1D)
    if not hasattr(_m_transformers_pytorch_utils, "prune_conv1d_layer"):
        setattr(_m_transformers_pytorch_utils, "prune_conv1d_layer", prune_conv1d_layer)
    if not hasattr(_m_transformers_pytorch_utils, "prune_layer"):
        setattr(_m_transformers_pytorch_utils, "prune_layer", prune_layer)
    if not hasattr(_m_transformers_pytorch_utils, "find_pruneable_heads_and_indices"):
        setattr(_m_transformers_pytorch_utils, "find_pruneable_heads_and_indices", find_pruneable_heads_and_indices)
    if not hasattr(_m_transformers_pytorch_utils, "isin_mps_friendly"):
        setattr(_m_transformers_pytorch_utils, "isin_mps_friendly", isin_mps_friendly)
    _m_transformers_generation_beam_constraints = _ensure_module("transformers.generation.beam_constraints")
    if not hasattr(_m_transformers_generation_beam_constraints, "Constraint"):
        setattr(_m_transformers_generation_beam_constraints, "Constraint", Constraint)
    if not hasattr(_m_transformers_generation_beam_constraints, "PhrasalConstraint"):
        setattr(_m_transformers_generation_beam_constraints, "PhrasalConstraint", PhrasalConstraint)
    if not hasattr(_m_transformers_generation_beam_constraints, "DisjunctiveTrie"):
        setattr(_m_transformers_generation_beam_constraints, "DisjunctiveTrie", DisjunctiveTrie)
    if not hasattr(_m_transformers_generation_beam_constraints, "DisjunctiveConstraint"):
        setattr(_m_transformers_generation_beam_constraints, "DisjunctiveConstraint", DisjunctiveConstraint)
    if not hasattr(_m_transformers_generation_beam_constraints, "ConstraintListState"):
        setattr(_m_transformers_generation_beam_constraints, "ConstraintListState", ConstraintListState)
    _m_transformers_generation_beam_search = _ensure_module("transformers.generation.beam_search")
    if not hasattr(_m_transformers_generation_beam_search, "BeamScorer"):
        setattr(_m_transformers_generation_beam_search, "BeamScorer", BeamScorer)
    if not hasattr(_m_transformers_generation_beam_search, "BeamSearchScorer"):
        setattr(_m_transformers_generation_beam_search, "BeamSearchScorer", BeamSearchScorer)
    if not hasattr(_m_transformers_generation_beam_search, "ConstrainedBeamSearchScorer"):
        setattr(_m_transformers_generation_beam_search, "ConstrainedBeamSearchScorer", ConstrainedBeamSearchScorer)
    _m_transformers_generation_logits_process = _ensure_module("transformers.generation.logits_process")
    if not hasattr(_m_transformers_generation_logits_process, "HammingDiversityLogitsProcessor"):
        setattr(_m_transformers_generation_logits_process, "HammingDiversityLogitsProcessor", HammingDiversityLogitsProcessor)
    _m_transformers_utils = _ensure_module("transformers.utils")
    if not hasattr(_m_transformers_utils, "is_offline_mode"):
        setattr(_m_transformers_utils, "is_offline_mode", is_offline_mode)
    if not hasattr(_m_transformers_utils, "is_remote_url"):
        setattr(_m_transformers_utils, "is_remote_url", is_remote_url)
    if not hasattr(_m_transformers_utils, "download_url"):
        setattr(_m_transformers_utils, "download_url", download_url)
    _m_transformers_utils = _ensure_module("transformers.utils")
    if not hasattr(_m_transformers_utils, "is_torch_sdpa_available"):
        setattr(_m_transformers_utils, "is_torch_sdpa_available", is_torch_sdpa_available)
    if not hasattr(_m_transformers_utils, "is_torch_npu_available"):
        setattr(_m_transformers_utils, "is_torch_npu_available", is_torch_npu_available)
    if not hasattr(_m_transformers_utils, "is_torch_mlu_available"):
        setattr(_m_transformers_utils, "is_torch_mlu_available", is_torch_mlu_available)
    if not hasattr(_m_transformers_utils, "is_safetensors_available"):
        setattr(_m_transformers_utils, "is_safetensors_available", is_safetensors_available)
    _m_transformers_utils_model_parallel_utils = _ensure_module("transformers.utils.model_parallel_utils")
    if not hasattr(_m_transformers_utils_model_parallel_utils, "assert_device_map"):
        setattr(_m_transformers_utils_model_parallel_utils, "assert_device_map", assert_device_map)
    if not hasattr(_m_transformers_utils_model_parallel_utils, "get_device_map"):
        setattr(_m_transformers_utils_model_parallel_utils, "get_device_map", get_device_map)
    _m_transformers_dynamic_module_utils = _ensure_module("transformers.dynamic_module_utils")
    if not hasattr(_m_transformers_dynamic_module_utils, "custom_object_save"):
        setattr(_m_transformers_dynamic_module_utils, "custom_object_save", custom_object_save)
    _m_transformers_tokenization_utils = _ensure_module("transformers.tokenization_utils")
    if not hasattr(_m_transformers_tokenization_utils, "Trie"):
        setattr(_m_transformers_tokenization_utils, "Trie", Trie)
    if not hasattr(_m_transformers_tokenization_utils, "ExtensionsTrie"):
        setattr(_m_transformers_tokenization_utils, "ExtensionsTrie", ExtensionsTrie)
    _m_transformers_utils = _ensure_module("transformers.utils")
    if not hasattr(_m_transformers_utils, "FLAX_WEIGHTS_NAME"):
        setattr(_m_transformers_utils, "FLAX_WEIGHTS_NAME", "flax_model.msgpack")
    _m_transformers_utils = _ensure_module("transformers.utils")
    if not hasattr(_m_transformers_utils, "TF2_WEIGHTS_NAME"):
        setattr(_m_transformers_utils, "TF2_WEIGHTS_NAME", "tf_model.h5")
    _m_transformers_utils = _ensure_module("transformers.utils")
    if not hasattr(_m_transformers_utils, "TF_WEIGHTS_NAME"):
        setattr(_m_transformers_utils, "TF_WEIGHTS_NAME", "tf_model.h5")

    # ---- TF / Flax checkpoint 搬运函数：5.x 移除，TTS 永不触达 → stub ----
    _mf = _ensure_module("transformers.modeling_flax_pytorch_utils")
    if not hasattr(_mf, "load_flax_checkpoint_in_pytorch_model"):
        def load_flax_checkpoint_in_pytorch_model(*args, **kwargs):  # noqa: ANN001, ANN202, D401
            raise ImportError("Flax checkpoint support removed in transformers 5.x; TTS path never uses it.")
        setattr(_mf, "load_flax_checkpoint_in_pytorch_model", load_flax_checkpoint_in_pytorch_model)
    _mt = _ensure_module("transformers.modeling_tf_pytorch_utils")
    if not hasattr(_mt, "load_tf2_checkpoint_in_pytorch_model"):
        def load_tf2_checkpoint_in_pytorch_model(*args, **kwargs):  # noqa: ANN001, ANN202, D401
            raise ImportError("TF2 checkpoint support removed in transformers 5.x; TTS path never uses it.")
        setattr(_mt, "load_tf2_checkpoint_in_pytorch_model", load_tf2_checkpoint_in_pytorch_model)


_install_all()


# ---- bigvgan 兼容 ---------------------------------------------------------------
# 注意：indextts 用的不是 pip 包 `bigvgan`，而是其内置副本
# `indextts.s2mel.modules.bigvgan.bigvgan`（见 infer_v2_5.py:30 / :229）。
# 它的 BigVGAN 继承 4.52 时代 transformers 的 PreTrainedModel 副本，`_from_pretrained`
# 把 `proxies` / `resume_download` 声明为必填 keyword-only；5.x 的调用链不再传这两个
# 参数 → TypeError。此处注入旧版默认语义（proxies=None、resume_download=False）。
_BIGVGAN_CANDIDATES = (
    "indextts.s2mel.modules.bigvgan.bigvgan",
    "indextts.BigVGAN.bigvgan",
    "bigvgan",
)


def patch_bigvgan_from_pretrained() -> bool:
    """给 indextts 内置 bigvgan 的 ``_from_pretrained`` 补上 5.x 不再传的两个 kwarg。

    须在 ``indextts`` 推理模块导入之后调用（那时 bigvgan 才在 sys.modules）。
    返回是否真的打了补丁（失败不静默：调用方会在加载失败时看到日志）。
    """
    import importlib
    import inspect

    for mod_name in _BIGVGAN_CANDIDATES:
        try:
            mod = importlib.import_module(mod_name)
        except (ImportError, ModuleNotFoundError):
            continue
        cls = getattr(mod, "BigVGAN", None)
        if cls is None:
            continue
        raw = cls.__dict__.get("_from_pretrained")
        if raw is None:
            logger.info("[backfill] %s.BigVGAN 未自定义 _from_pretrained，无需补丁", mod_name)
            return True
        fn = raw.__func__ if isinstance(raw, classmethod) else raw
        params = inspect.signature(fn).parameters
        miss = [n for n in ("proxies", "resume_download")
                if n in params and params[n].default is inspect.Parameter.empty]
        if not miss:
            return True  # 已有默认值
        orig = fn

        def _patched(cls_, *args, proxies=None, resume_download=False, **kwargs):  # noqa: ANN001, ANN202
            return orig(cls_, *args, proxies=proxies, resume_download=resume_download, **kwargs)

        _patched.__doc__ = orig.__doc__
        setattr(cls, "_from_pretrained", classmethod(_patched))
        logger.info("[backfill] 已为 %s.BigVGAN._from_pretrained 注入默认值 %s", mod_name, miss)
        return True
    logger.warning("[backfill] 未找到 indextts 内置 bigvgan 模块（候选：%s）", _BIGVGAN_CANDIDATES)
    return False


# ---- PretrainedConfig 生成参数方法（4.x 有、5.x 删除）------------------------------
# indextts vendored 的 `indextts/gpt/transformers_generation_utils.py` 是 4.x 副本：
#   - 第 1489 行调 `self.config._get_non_default_generation_parameters()`
#   - 第 1591 行读 `self.config._pre_quantization_dtype`
# 5.20.2 已把前两者从 PretrainedConfig 整体删除（连带 `_get_global_generation_defaults`）
# → `AttributeError: 'GPT2Config' object has no attribute ...`。以下按 4.52.1 原样回填。

_GLOBAL_GENERATION_DEFAULTS = {
    "max_length": 20,
    "min_length": 0,
    "do_sample": False,
    "early_stopping": False,
    "num_beams": 1,
    "num_beam_groups": 1,
    "diversity_penalty": 0.0,
    "temperature": 1.0,
    "top_k": 50,
    "top_p": 1.0,
    "typical_p": 1.0,
    "repetition_penalty": 1.0,
    "length_penalty": 1.0,
    "no_repeat_ngram_size": 0,
    "encoder_no_repeat_ngram_size": 0,
    "bad_words_ids": None,
    "num_return_sequences": 1,
    "output_scores": False,
    "return_dict_in_generate": False,
    "forced_bos_token_id": None,
    "forced_eos_token_id": None,
    "remove_invalid_values": False,
    "exponential_decay_length_penalty": None,
    "suppress_tokens": None,
    "begin_suppress_tokens": None,
}


def _get_global_generation_defaults() -> dict:
    """4.52.1 ``PretrainedConfig._get_global_generation_defaults`` 原样回填。"""
    return dict(_GLOBAL_GENERATION_DEFAULTS)


def _get_non_default_generation_parameters(self) -> dict:
    """4.52.1 ``PretrainedConfig._get_non_default_generation_parameters`` 原样回填。"""
    non_default_generation_parameters = {}
    decoder_attribute_name = None

    # 复合模型没有可实例化的默认 config，退化为用其文本子 config 默认值比较
    try:
        default_config = self.__class__()
    except ValueError:
        decoder_config = self.get_text_config(decoder=True)
        if decoder_config is not self:
            default_config = decoder_config.__class__()
        else:
            default_config = None

    self_decoder_config = self if decoder_attribute_name is None else getattr(self, decoder_attribute_name)

    for parameter_name, default_global_value in self._get_global_generation_defaults().items():
        if hasattr(self_decoder_config, parameter_name):
            is_default_in_config = is_default_generation_value = None
            parameter_value = getattr(self_decoder_config, parameter_name)
            if parameter_value is None:
                continue
            if default_config is not None:
                is_default_in_config = parameter_value == getattr(default_config, parameter_name)
            else:
                is_default_generation_value = parameter_value == default_global_value

            is_non_default = (is_default_in_config is False) or (
                is_default_in_config is None and is_default_generation_value is False
            )
            if is_non_default:
                non_default_generation_parameters[parameter_name] = getattr(self_decoder_config, parameter_name)

    return non_default_generation_parameters


def _patch_pretrained_config_generation_params() -> None:
    from transformers import GenerationConfig, PretrainedConfig

    added = []
    if not hasattr(PretrainedConfig, "_get_global_generation_defaults"):
        PretrainedConfig._get_global_generation_defaults = staticmethod(_get_global_generation_defaults)
        added.append("_get_global_generation_defaults")
    if not hasattr(PretrainedConfig, "_get_non_default_generation_parameters"):
        PretrainedConfig._get_non_default_generation_parameters = _get_non_default_generation_parameters
        added.append("_get_non_default_generation_parameters")
    if not hasattr(PretrainedConfig, "_pre_quantization_dtype"):
        PretrainedConfig._pre_quantization_dtype = None
        added.append("_pre_quantization_dtype")
    # 4.52 的 `return_legacy_cache`（旧 cache 格式回退开关）在 5.x 被移除。
    # vendored generation_utils:2359/2366 读取它；5.x 已无 legacy cache 转换通道，
    # 默认 False 即"不做转换"，与现代 transformers 行为一致。
    if not hasattr(GenerationConfig, "return_legacy_cache"):
        GenerationConfig.return_legacy_cache = False
        added.append("GenerationConfig.return_legacy_cache")
    if added:
        logger.info("[backfill] 配置对象已回填 4.52 生成 API: %s", added)


_patch_pretrained_config_generation_params()
