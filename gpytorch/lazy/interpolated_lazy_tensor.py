from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import torch
from .block_diag_lazy_tensor import BlockDiagLazyTensor
from .lazy_tensor import LazyTensor
from .non_lazy_tensor import NonLazyTensor
from .root_lazy_tensor import RootLazyTensor
from ..utils import sparse
from ..utils.interpolation import left_interp, left_t_interp
from ..utils.sparse import bdsmm
from .. import beta_features


class InterpolatedLazyTensor(LazyTensor):
    def __init__(
        self,
        base_lazy_tensor,
        left_interp_indices=None,
        left_interp_values=None,
        right_interp_indices=None,
        right_interp_values=None,
    ):
        if torch.is_tensor(base_lazy_tensor):
            base_lazy_tensor = NonLazyTensor(base_lazy_tensor)

        if left_interp_indices is None:
            num_rows = base_lazy_tensor.size()[-2]
            left_interp_indices = torch.arange(0, num_rows, dtype=torch.long, device=base_lazy_tensor.device)
            left_interp_indices.unsqueeze_(-1)
            if base_lazy_tensor.ndimension() == 3:
                left_interp_indices = left_interp_indices.unsqueeze(0).expand(base_lazy_tensor.size(0), num_rows, 1)
            elif right_interp_indices is not None and right_interp_indices.ndimension() == 3:
                left_interp_indices = left_interp_indices.unsqueeze(0).expand(right_interp_indices.size(0), num_rows, 1)
        else:
            if left_interp_indices.dim() != base_lazy_tensor.ndimension():
                raise RuntimeError(
                    "Expected left_interp_indices ({}) to have the same number of dimensions as "
                    "base_lazy_Tensor ({})".format(left_interp_indices.size(), base_lazy_tensor.size())
                )

        if left_interp_values is None:
            left_interp_values = torch.ones(
                left_interp_indices.size(), dtype=base_lazy_tensor.dtype, device=base_lazy_tensor.device
            )
        else:
            if left_interp_indices.size() != left_interp_values.size():
                raise RuntimeError(
                    "Expected left_interp_indices ({}) to have the same size as "
                    "left_interp_values ({})".format(left_interp_indices.size(), left_interp_values.size())
                )

        if right_interp_indices is None:
            num_rows = base_lazy_tensor.size()[-2]
            right_interp_indices = torch.arange(0, num_rows, dtype=torch.long, device=base_lazy_tensor.device)
            right_interp_indices.unsqueeze_(-1)
            if base_lazy_tensor.ndimension() == 3:
                right_interp_indices = right_interp_indices.unsqueeze(0).expand(base_lazy_tensor.size(0), num_rows, 1)
            elif left_interp_indices.ndimension() == 3:
                right_interp_indices = right_interp_indices.unsqueeze(0).expand(
                    left_interp_indices.size(0), num_rows, 1
                )
        else:
            if left_interp_indices.dim() != base_lazy_tensor.ndimension():
                raise RuntimeError(
                    "Expected left_interp_indices ({}) to have the same number of dimensions as "
                    "base_lazy_Tensor ({})".format(left_interp_indices.size(), base_lazy_tensor.size())
                )

        if right_interp_values is None:
            right_interp_values = torch.ones(
                right_interp_indices.size(), dtype=base_lazy_tensor.dtype, device=base_lazy_tensor.device
            )
        else:
            if left_interp_indices.size() != left_interp_values.size():
                raise RuntimeError(
                    "Expected left_interp_indices ({}) to have the same size as "
                    "left_interp_values ({})".format(left_interp_indices.size(), left_interp_values.size())
                )

        super(InterpolatedLazyTensor, self).__init__(
            base_lazy_tensor, left_interp_indices, left_interp_values, right_interp_indices, right_interp_values
        )
        self.base_lazy_tensor = base_lazy_tensor
        self.left_interp_indices = left_interp_indices
        self.left_interp_values = left_interp_values
        self.right_interp_indices = right_interp_indices
        self.right_interp_values = right_interp_values

    def _approx_diag(self):
        base_diag_root = self.base_lazy_tensor.diag().sqrt()
        left_res = left_interp(self.left_interp_indices, self.left_interp_values, base_diag_root.unsqueeze(-1))
        right_res = left_interp(self.right_interp_indices, self.right_interp_values, base_diag_root.unsqueeze(-1))
        res = left_res * right_res
        return res.squeeze(-1)

    def _matmul(self, rhs):
        # Get sparse tensor representations of left/right interp matrices
        left_interp_t = self._sparse_left_interp_t(self.left_interp_indices, self.left_interp_values)
        right_interp_t = self._sparse_right_interp_t(self.right_interp_indices, self.right_interp_values)

        if rhs.ndimension() == 1:
            is_vector = True
            rhs = rhs.unsqueeze(-1)
        else:
            is_vector = False

        # right_interp^T * rhs
        right_interp_res = bdsmm(right_interp_t, rhs)

        # base_lazy_tensor * right_interp^T * rhs
        base_res = self.base_lazy_tensor._matmul(right_interp_res)

        # left_interp * base_lazy_tensor * right_interp^T * rhs
        left_interp_mat = left_interp_t.transpose(-1, -2)
        res = bdsmm(left_interp_mat, base_res)

        # Squeeze if necessary
        if is_vector:
            res = res.squeeze(-1)
        return res

    def _t_matmul(self, rhs):
        # Get sparse tensor representations of left/right interp matrices
        left_interp_t = self._sparse_left_interp_t(self.left_interp_indices, self.left_interp_values)
        right_interp_t = self._sparse_right_interp_t(self.right_interp_indices, self.right_interp_values)

        if rhs.ndimension() == 1:
            is_vector = True
            rhs = rhs.unsqueeze(-1)
        else:
            is_vector = False

        # right_interp^T * rhs
        left_interp_res = bdsmm(left_interp_t, rhs)

        # base_lazy_tensor * right_interp^T * rhs
        base_res = self.base_lazy_tensor._t_matmul(left_interp_res)

        # left_interp * base_lazy_tensor * right_interp^T * rhs
        right_interp_mat = right_interp_t.transpose(-1, -2)
        res = bdsmm(right_interp_mat, base_res)

        # Squeeze if necessary
        if is_vector:
            res = res.squeeze(-1)
        return res

    def _quad_form_derivative(self, left_vecs, right_vecs):
        # Get sparse tensor representations of left/right interp matrices
        left_interp_t = self._sparse_left_interp_t(self.left_interp_indices, self.left_interp_values)
        right_interp_t = self._sparse_right_interp_t(self.right_interp_indices, self.right_interp_values)

        if left_vecs.ndimension() == 1:
            left_vecs = left_vecs.unsqueeze(1)
            right_vecs = right_vecs.unsqueeze(1)

        # base_lazy_tensor grad
        left_res = bdsmm(left_interp_t, left_vecs)
        right_res = bdsmm(right_interp_t, right_vecs)
        base_lv_grad = list(self.base_lazy_tensor._quad_form_derivative(left_res, right_res))

        # left_interp_values grad
        n_vecs = right_res.size(-1)
        n_left_rows = self.left_interp_indices.size(-2)
        n_right_rows = self.right_interp_indices.size(-2)
        n_left_interp = self.left_interp_indices.size(-1)
        n_right_interp = self.right_interp_indices.size(-1)
        n_inducing = right_res.size(-2)
        if self.left_interp_indices.ndimension() == 3:
            batch_size = self.left_interp_indices.size(0)

        # left_interp_values grad
        right_interp_right_res = self.base_lazy_tensor._matmul(right_res).contiguous()
        if self.left_interp_indices.ndimension() == 3:
            batch_offset = torch.arange(0, batch_size, dtype=torch.long, device=self.device)
            batch_offset.unsqueeze_(-1).unsqueeze_(-1).mul_(n_inducing)

            batched_left_interp_indices = (self.left_interp_indices + batch_offset).view(-1)
            flattened_right_interp_right_res = right_interp_right_res.view(batch_size * n_inducing, n_vecs)

            selected_right_vals = flattened_right_interp_right_res.index_select(0, batched_left_interp_indices)
            selected_right_vals = selected_right_vals.view(batch_size, n_left_rows, n_left_interp, n_vecs)
        else:
            selected_right_vals = right_interp_right_res.index_select(0, self.left_interp_indices.view(-1))
            selected_right_vals = selected_right_vals.view(n_left_rows, n_left_interp, n_vecs)
        left_values_grad = (selected_right_vals * left_vecs.unsqueeze(-2)).sum(-1)

        # right_interp_values_grad
        left_interp_left_res = self.base_lazy_tensor._t_matmul(left_res).contiguous()
        if self.right_interp_indices.ndimension() == 3:
            batch_offset = torch.arange(0, batch_size, dtype=torch.long, device=self.device)
            batch_offset.unsqueeze_(-1).unsqueeze_(-1).mul_(n_inducing)

            batched_right_interp_indices = (self.right_interp_indices + batch_offset).view(-1)
            flattened_left_interp_left_res = left_interp_left_res.view(batch_size * n_inducing, n_vecs)

            selected_left_vals = flattened_left_interp_left_res.index_select(0, batched_right_interp_indices)
            selected_left_vals = selected_left_vals.view(batch_size, n_right_rows, n_right_interp, n_vecs)
        else:
            selected_left_vals = left_interp_left_res.index_select(0, self.right_interp_indices.view(-1))
            selected_left_vals = selected_left_vals.view(n_right_rows, n_right_interp, n_vecs)
        right_values_grad = (selected_left_vals * right_vecs.unsqueeze(-2)).sum(-1)

        # Return zero grad for interp indices
        res = tuple(
            base_lv_grad
            + [
                torch.zeros_like(self.left_interp_indices),
                left_values_grad,
                torch.zeros_like(self.right_interp_indices),
                right_values_grad,
            ]
        )
        return res

    def _size(self):
        if self.left_interp_indices.ndimension() == 3:
            return torch.Size(
                (self.left_interp_indices.size(0), self.left_interp_indices.size(1), self.right_interp_indices.size(1))
            )
        else:
            return torch.Size((self.left_interp_indices.size(0), self.right_interp_indices.size(0)))

    def _transpose_nonbatch(self):
        res = self.__class__(
            self.base_lazy_tensor.transpose(-1, -2),
            self.right_interp_indices,
            self.right_interp_values,
            self.left_interp_indices,
            self.left_interp_values,
            **self._kwargs
        )
        return res

    def _batch_get_indices(self, batch_indices, left_indices, right_indices):
        left_interp_indices = self.left_interp_indices[batch_indices, left_indices, :]
        left_interp_values = self.left_interp_values[batch_indices, left_indices, :]
        right_interp_indices = self.right_interp_indices[batch_indices, right_indices, :]
        right_interp_values = self.right_interp_values[batch_indices, right_indices, :]

        n_data, n_interp = left_interp_indices.size()

        # Batch compute the non-zero values of the outer products w_left^k w_right^k^T
        left_interp_values = left_interp_values.unsqueeze(-1)
        right_interp_values = right_interp_values.unsqueeze(-2)
        interp_values = torch.matmul(left_interp_values, right_interp_values)

        # Batch compute values that will be non-zero for row k
        left_interp_indices = left_interp_indices.unsqueeze(-1).expand(n_data, n_interp, n_interp)
        right_interp_indices = right_interp_indices.unsqueeze(-2).expand(n_data, n_interp, n_interp)
        left_interp_indices = left_interp_indices.contiguous()
        right_interp_indices = right_interp_indices.contiguous()
        batch_indices = batch_indices.unsqueeze(1).repeat(1, n_interp ** 2).view(-1)
        base_var_vals = self.base_lazy_tensor._batch_get_indices(
            batch_indices, left_interp_indices.view(-1), right_interp_indices.view(-1)
        )
        base_var_vals = base_var_vals.view(left_interp_indices.size())
        res = (interp_values * base_var_vals).sum(-1).sum(-1)
        return res

    def _get_indices(self, left_indices, right_indices):
        left_interp_indices = self.left_interp_indices[left_indices, :]
        left_interp_values = self.left_interp_values[left_indices, :]
        right_interp_indices = self.right_interp_indices[right_indices, :]
        right_interp_values = self.right_interp_values[right_indices, :]

        n_data, n_interp = left_interp_indices.size()

        # Batch compute the non-zero values of the outer products w_left^k w_right^k^T
        left_interp_values = left_interp_values.unsqueeze(-1)
        right_interp_values = right_interp_values.unsqueeze(-2)
        interp_values = torch.matmul(left_interp_values, right_interp_values)

        # Batch compute values that will be non-zero for row k
        if left_interp_indices.ndimension() == 3:
            left_interp_indices = left_interp_indices.unsqueeze(-1).expand(n_data, n_interp, n_interp).contiguous()
            right_interp_indices = right_interp_indices.unsqueeze(-2).expand(n_data, n_interp, n_interp).contiguous()
        else:
            left_interp_indices = left_interp_indices.unsqueeze(-1).expand(n_data, n_interp, n_interp).contiguous()
            right_interp_indices = right_interp_indices.unsqueeze(-2).expand(n_data, n_interp, n_interp).contiguous()
        base_var_vals = self.base_lazy_tensor._get_indices(left_interp_indices.view(-1), right_interp_indices.view(-1))
        base_var_vals = base_var_vals.view(left_interp_indices.size())
        res = (interp_values * base_var_vals).sum(-1).sum(-1)
        return res

    def _sparse_left_interp_t(self, left_interp_indices_tensor, left_interp_values_tensor):
        if hasattr(self, "_sparse_left_interp_t_memo"):
            if torch.equal(self._left_interp_indices_memo, left_interp_indices_tensor) and torch.equal(
                self._left_interp_values_memo, left_interp_values_tensor
            ):
                return self._sparse_left_interp_t_memo

        left_interp_t = sparse.make_sparse_from_indices_and_values(
            left_interp_indices_tensor, left_interp_values_tensor, self.base_lazy_tensor.size()[-1]
        )
        self._left_interp_indices_memo = left_interp_indices_tensor
        self._left_interp_values_memo = left_interp_values_tensor
        self._sparse_left_interp_t_memo = left_interp_t
        return self._sparse_left_interp_t_memo

    def _sparse_right_interp_t(self, right_interp_indices_tensor, right_interp_values_tensor):
        if hasattr(self, "_sparse_right_interp_t_memo"):
            if torch.equal(self._right_interp_indices_memo, right_interp_indices_tensor) and torch.equal(
                self._right_interp_values_memo, right_interp_values_tensor
            ):
                return self._sparse_right_interp_t_memo

        right_interp_t = sparse.make_sparse_from_indices_and_values(
            right_interp_indices_tensor, right_interp_values_tensor, self.base_lazy_tensor.size()[-1]
        )
        self._right_interp_indices_memo = right_interp_indices_tensor
        self._right_interp_values_memo = right_interp_values_tensor
        self._sparse_right_interp_t_memo = right_interp_t
        return self._sparse_right_interp_t_memo

    def diag(self):
        if isinstance(self.base_lazy_tensor, RootLazyTensor):
            res = left_interp(self.left_interp_indices, self.left_interp_values, self.base_lazy_tensor.root.evaluate())
            return res.pow(2).sum(-1)
        else:
            batch_size = None
            n_data = None
            n_interp = None
            if self.left_interp_indices.ndimension() == 3:
                batch_size, n_data, n_interp = self.left_interp_indices.size()
            else:
                n_data, n_interp = self.left_interp_indices.size()

            # Batch compute the non-zero values of the outer products w_left^k w_right^k^T
            left_interp_values = self.left_interp_values.unsqueeze(-1)
            right_interp_values = self.right_interp_values.unsqueeze(-2)
            interp_values = torch.matmul(left_interp_values, right_interp_values).view(-1)

            # Batch compute indicies that will be non-zero for row k
            if batch_size is None:
                left_interp_indices = self.left_interp_indices.unsqueeze(-1).expand(n_data, n_interp, n_interp)
                right_interp_indices = self.right_interp_indices.unsqueeze(-2).expand(n_data, n_interp, n_interp)
            else:
                left_interp_indices = self.left_interp_indices.unsqueeze(-1).expand(
                    batch_size, n_data, n_interp, n_interp
                )
                right_interp_indices = self.right_interp_indices.unsqueeze(-2).expand(
                    batch_size, n_data, n_interp, n_interp
                )

            left_interp_indices = left_interp_indices.contiguous().view(-1)
            right_interp_indices = right_interp_indices.contiguous().view(-1)

            if self.base_lazy_tensor.ndimension() == 3:
                batch_indices = torch.arange(0, batch_size, dtype=torch.long, device=self.device).unsqueeze(-1)
                batch_indices = batch_indices.repeat(1, n_data * n_interp * n_interp).view(-1)
                base_var_vals = self.base_lazy_tensor._batch_get_indices(
                    batch_indices, left_interp_indices, right_interp_indices
                )
            else:
                base_var_vals = self.base_lazy_tensor._get_indices(left_interp_indices, right_interp_indices)

            res = interp_values * base_var_vals
            if batch_size is None:
                res = res.view(n_data, -1).sum(-1)
            else:
                res = res.view(batch_size, n_data, -1).sum(-1)
            return res

    def exact_predictive_mean(
        self, full_mean, train_labels, num_train, likelihood, precomputed_cache=None, non_batch_train=False
    ):
        from ..distributions import MultivariateNormal

        if precomputed_cache is None:
            train_interp_indices = self.left_interp_indices.narrow(-2, 0, num_train)
            train_interp_values = self.left_interp_values.narrow(-2, 0, num_train)

            # Get inverse root
            train_train_covar = self.__class__(
                self.base_lazy_tensor,
                train_interp_indices,
                train_interp_values,
                train_interp_indices,
                train_interp_values,
            )

            train_mean = full_mean.narrow(-1, 0, train_train_covar.size(-1))

            mvn = likelihood(MultivariateNormal(train_mean, train_train_covar))
            train_mean, train_train_covar = mvn.mean, mvn.lazy_covariance_matrix

            train_train_covar_inv_labels = train_train_covar.inv_matmul((train_labels - train_mean).unsqueeze(-1))

            # New root factor
            base_size = self.base_lazy_tensor.size(-1)
            precomputed_cache = self.base_lazy_tensor.matmul(
                left_t_interp(train_interp_indices, train_interp_values, train_train_covar_inv_labels, base_size)
            )

            # Prevent backprop through this variable
            precomputed_cache = precomputed_cache.detach()

        # Compute the exact predictive posterior
        n_test = self.size(-1) - num_train
        test_mean = full_mean.narrow(-1, num_train, n_test)
        test_interp_indices = self.left_interp_indices.narrow(-2, num_train, n_test)
        test_interp_values = self.left_interp_values.narrow(-2, num_train, n_test)
        res = left_interp(test_interp_indices, test_interp_values, precomputed_cache).squeeze(-1) + test_mean
        return res, precomputed_cache

    def _exact_predictive_covar_inv_quad_form_cache(self, train_train_covar_inv_root, test_train_covar):
        train_interp_indices = test_train_covar.right_interp_indices
        train_interp_values = test_train_covar.right_interp_values
        base_lazy_tensor = test_train_covar.base_lazy_tensor
        base_size = base_lazy_tensor.size(-1)
        res = base_lazy_tensor.matmul(
            left_t_interp(train_interp_indices, train_interp_values, train_train_covar_inv_root, base_size)
        )
        return res

    def _exact_predictive_covar_inv_quad_form_root(self, precomputed_cache, test_train_covar):
        # Here the precomputed cache represents K_UU W S,
        # where S S^T = (K_XX + sigma^2 I)^-1
        test_interp_indices = test_train_covar.left_interp_indices
        test_interp_values = test_train_covar.left_interp_values
        res = left_interp(test_interp_indices, test_interp_values, precomputed_cache)
        return res

    def exact_predictive_covar(self, num_train, likelihood, precomputed_cache=None, non_batch_train=False):
        from ..distributions import MultivariateNormal

        if not beta_features.fast_pred_var.on() and not beta_features.fast_pred_samples.on():
            return super(InterpolatedLazyTensor, self).exact_predictive_covar(num_train, likelihood, precomputed_cache)

        n_test = self.size(-2) - num_train
        train_interp_indices = self.left_interp_indices.narrow(-2, 0, num_train)
        train_interp_values = self.left_interp_values.narrow(-2, 0, num_train)
        test_interp_indices = self.left_interp_indices.narrow(-2, num_train, n_test)
        test_interp_values = self.left_interp_values.narrow(-2, num_train, n_test)
        test_train_covar = self.__class__(
            self.base_lazy_tensor, test_interp_indices, test_interp_values, train_interp_indices, train_interp_values
        )

        if (
            precomputed_cache is None
            or (beta_features.fast_pred_samples.on() and precomputed_cache[0] is None)
            or (not beta_features.fast_pred_samples.on() and precomputed_cache[1] is None)
        ):
            # Get inverse root
            train_train_covar = self.__class__(
                self.base_lazy_tensor,
                train_interp_indices,
                train_interp_values,
                train_interp_indices,
                train_interp_values,
            )

            grv = MultivariateNormal(torch.zeros(1), train_train_covar)
            train_train_covar = likelihood(grv).lazy_covariance_matrix

            # Get probe vectors for inverse root
            num_probe_vectors = beta_features.fast_pred_var.num_probe_vectors()
            batch_size = train_interp_indices.size(0)
            n_inducing = self.base_lazy_tensor.size(-1)
            vector_indices = torch.randperm(n_inducing).type_as(train_interp_indices)
            probe_vector_indices = vector_indices[:num_probe_vectors]
            test_vector_indices = vector_indices[num_probe_vectors : 2 * num_probe_vectors]

            probe_interp_indices = probe_vector_indices.unsqueeze(1)
            probe_test_interp_indices = test_vector_indices.unsqueeze(1)
            probe_interp_values = torch.ones(num_probe_vectors, 1, dtype=self.dtype, device=self.device)
            if train_interp_indices.ndimension() == 3:
                probe_interp_indices = probe_interp_indices.unsqueeze(0).expand(batch_size, num_probe_vectors, 1)
                probe_test_interp_indices = probe_test_interp_indices.unsqueeze(0)
                probe_test_interp_indices = probe_test_interp_indices.expand(batch_size, num_probe_vectors, 1)
                probe_interp_values = probe_interp_values.unsqueeze(0).expand(batch_size, num_probe_vectors, 1)

            probe_vectors = InterpolatedLazyTensor(
                self.base_lazy_tensor,
                train_interp_indices,
                train_interp_values,
                probe_interp_indices,
                probe_interp_values,
            ).evaluate()
            test_vectors = InterpolatedLazyTensor(
                self.base_lazy_tensor,
                train_interp_indices,
                train_interp_values,
                probe_test_interp_indices,
                probe_interp_values,
            ).evaluate()

            # Get inverse root
            train_train_covar_inv_root = train_train_covar.root_inv_decomposition(probe_vectors, test_vectors).root
            train_train_covar_inv_root = train_train_covar_inv_root.evaluate()

            # New root factor
            root = self._exact_predictive_covar_inv_quad_form_cache(train_train_covar_inv_root, test_train_covar)

            # Precomputed factor
            if beta_features.fast_pred_samples.on():
                inside = self.base_lazy_tensor + RootLazyTensor(root).mul(-1)
                inside_root = inside.root_decomposition().root.evaluate()
                # Prevent backprop through this variable
                inside_root = inside_root.detach()
                precomputed_cache = inside_root, None
            else:
                # Prevent backprop through this variable
                root = root.detach()
                precomputed_cache = None, root

        # Compute the exact predictive posterior
        if beta_features.fast_pred_samples.on():
            res = self._exact_predictive_covar_inv_quad_form_root(precomputed_cache[0], test_train_covar)
            res = RootLazyTensor(res)
        else:
            test_test_prior_covar = InterpolatedLazyTensor(
                self.base_lazy_tensor, test_interp_indices, test_interp_values, test_interp_indices, test_interp_values
            )
            root = left_interp(test_interp_indices, test_interp_values, precomputed_cache[1])
            res = test_test_prior_covar + RootLazyTensor(root).mul(-1)
        return res, precomputed_cache

    def matmul(self, tensor):
        # We're using a custom matmul here, because it is significantly faster than
        # what we get from the function factory.
        # The _matmul_closure is optimized for repeated calls, such as for inv_matmul

        if tensor.ndimension() == 1:
            is_vector = True
            tensor = tensor.unsqueeze(-1)
        else:
            is_vector = False

        # right_interp^T * tensor
        base_size = self.base_lazy_tensor.size(-1)
        right_interp_res = left_t_interp(self.right_interp_indices, self.right_interp_values, tensor, base_size)

        # base_lazy_tensor * right_interp^T * tensor
        base_res = self.base_lazy_tensor.matmul(right_interp_res)

        # left_interp * base_lazy_tensor * right_interp^T * tensor
        res = left_interp(self.left_interp_indices, self.left_interp_values, base_res)

        # Squeeze if necessary
        if is_vector:
            res = res.squeeze(-1)
        return res

    def mul(self, other):
        # We're using a custom method here - the constant mul is applied to the base_lazy tensor
        # This preserves the interpolated structure
        if not (torch.is_tensor(other) or isinstance(other, LazyTensor)) or (
            torch.is_tensor(other) and other.numel() == 1
        ):
            from .constant_mul_lazy_tensor import ConstantMulLazyTensor

            return self.__class__(
                ConstantMulLazyTensor(self.base_lazy_tensor, other),
                self.left_interp_indices,
                self.left_interp_values,
                self.right_interp_indices,
                self.right_interp_values,
            )
        else:
            return super(InterpolatedLazyTensor, self).mul(other)

    def sum_batch(self, sum_batch_size=None):
        left_interp_indices = self.left_interp_indices
        left_interp_values = self.left_interp_values
        right_interp_indices = self.right_interp_indices
        right_interp_values = self.right_interp_values

        n_interp = left_interp_indices.size(-1)
        n_left = left_interp_indices.size(-2)
        n_right = right_interp_indices.size(-2)

        # Deal with the two batch dimensions, if necessary
        if sum_batch_size is not None:
            left_interp_indices = left_interp_indices.view(-1, sum_batch_size, n_left, n_interp)
            left_interp_values = left_interp_values.view(-1, sum_batch_size, n_left, n_interp)
            right_interp_indices = right_interp_indices.view(-1, sum_batch_size, n_right, n_interp)
            right_interp_values = right_interp_values.view(-1, sum_batch_size, n_right, n_interp)

        # Increase interpolation indices appropriately
        factor = torch.arange(0, left_interp_indices.size(-3), dtype=torch.long, device=self.device)
        factor = factor.unsqueeze(-1).unsqueeze(-1)
        factor = factor * self.base_lazy_tensor.size(-1)
        if sum_batch_size is not None:
            factor = factor.unsqueeze(0)
        left_interp_indices = left_interp_indices.add(factor)
        right_interp_indices = right_interp_indices.add(factor)

        # Rearrange the indices and values
        if sum_batch_size is not None:
            left_interp_indices = left_interp_indices.permute(0, 2, 3, 1).contiguous()
            left_interp_indices = left_interp_indices.view(-1, n_left, n_interp * sum_batch_size)
            left_interp_values = left_interp_values.permute(0, 2, 3, 1).contiguous()
            left_interp_values = left_interp_values.view(-1, n_left, n_interp * sum_batch_size)
            right_interp_indices = right_interp_indices.permute(0, 2, 3, 1).contiguous()
            right_interp_indices = right_interp_indices.view(-1, n_right, n_interp * sum_batch_size)
            right_interp_values = right_interp_values.permute(0, 2, 3, 1).contiguous()
            right_interp_values = right_interp_values.view(-1, n_right, n_interp * sum_batch_size)
        else:
            left_interp_indices = left_interp_indices.permute(1, 2, 0).contiguous()
            left_interp_indices = left_interp_indices.view(n_left, -1)
            left_interp_values = left_interp_values.permute(1, 2, 0).contiguous()
            left_interp_values = left_interp_values.view(n_left, -1)
            right_interp_indices = right_interp_indices.permute(1, 2, 0).contiguous()
            right_interp_indices = right_interp_indices.view(n_right, -1)
            right_interp_values = right_interp_values.permute(1, 2, 0).contiguous()
            right_interp_values = right_interp_values.view(n_right, -1)

        # Make the base_lazy tensor block diagonal
        block_diag = BlockDiagLazyTensor(self.base_lazy_tensor, num_blocks=sum_batch_size)

        # Finally! We have an interpolated lazy tensor again
        return InterpolatedLazyTensor(
            block_diag, left_interp_indices, left_interp_values, right_interp_indices, right_interp_values
        )

    def zero_mean_mvn_samples(self, num_samples):
        base_samples = self.base_lazy_tensor.zero_mean_mvn_samples(num_samples)
        if self.ndimension() == 3:
            res = left_interp(
                self.left_interp_indices, self.left_interp_values, base_samples.permute(1, 2, 0).contiguous()
            )
            return res.permute(2, 0, 1).contiguous()
        else:
            res = left_interp(
                self.left_interp_indices, self.left_interp_values, base_samples.permute(1, 0).contiguous()
            )
            return res.permute(1, 0).contiguous()

    def _getitem_nonbatch(self, row_index, col_index, first_tensor_index_dim=None):
        """
        Given an index over rows and columns, gets those items from the LazyTensor.
        Implementing this is not necessary, but it improves performance

        Args:
            row_index (slice or LongTensor): index over rows
            col_index (slice or LongTensor): index over columns
            first_tensor_index_dim (int or None): first batch dim to have a tensor index (default: None)

        Returns:
            LazyTensor
        """
        ndimension = self.ndimension()

        left_interp_indices = self.left_interp_indices
        left_interp_values = self.left_interp_values
        right_interp_indices = self.right_interp_indices
        right_interp_values = self.right_interp_values

        batch_iter = [slice(None, None, None)] * (ndimension - 2)
        if first_tensor_index_dim is not None:
            batch_iter[first_tensor_index_dim] = torch.arange(
                0, self.size(first_tensor_index_dim), dtype=torch.long, device=self.device
            )

        left_index = (*batch_iter, row_index)
        left_interp_indices = left_interp_indices[left_index]
        left_interp_values = left_interp_values[left_index]
        if first_tensor_index_dim is not None and torch.is_tensor(row_index):
            left_interp_indices = left_interp_indices.unsqueeze(-2)
            left_interp_values = left_interp_values.unsqueeze(-2)

        right_index = (*batch_iter, col_index)
        right_interp_indices = right_interp_indices[right_index]
        right_interp_values = right_interp_values[right_index]
        if first_tensor_index_dim is not None and torch.is_tensor(col_index):
            right_interp_indices = right_interp_indices.unsqueeze(-2)
            right_interp_values = right_interp_values.unsqueeze(-2)

        res = self.__class__(
            self.base_lazy_tensor,
            left_interp_indices,
            left_interp_values,
            right_interp_indices,
            right_interp_values,
            **self._kwargs
        )
        return res
