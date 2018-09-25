import numpy as np
from scipy.sparse import coo_matrix, csr_matrix, eye, hstack, vstack, bmat, spdiags
from fealpy.fem.integral_alg import IntegralAlg
from scipy.sparse.linalg import cg, inv, dsolve, spsolve
class DarcyFDMModel():
    def __init__(self, pde, mesh):
        self.pde = pde
        self.mesh = mesh

        NE = mesh.number_of_edges()
        NC = mesh.number_of_cells()
        self.uh = np.zeros(NE, dtype=mesh.ftype)
        self.ph = np.zeros(NC, dtype=mesh.ftype)
        self.uI = np.zeros(NE, dtype=mesh.ftype) 
        
        isYDEdge = mesh.ds.y_direction_edge_flag()
        isXDEdge = mesh.ds.x_direction_edge_flag()
        bc = mesh.entity_barycenter('edge')
        self.uI[isYDEdge] = pde.velocity_x(bc[isYDEdge])
        self.uI[isXDEdge] = pde.velocity_y(bc[isXDEdge]) 
        pc = mesh.entity_barycenter('cell')
        self.pI = pde.pressure(pc)

        self.ph[0] = self.pI[0]
         

    def get_left_matrix(self):

        mesh = self.mesh
        NE = mesh.number_of_edges()
        NC = mesh.number_of_cells()

        itype = mesh.itype
        ftype = mesh.ftype

        idx = np.arange(NE)
        isBDEdge = mesh.ds.boundary_edge_flag()
        isYDEdge = mesh.ds.y_direction_edge_flag()
        isXDEdge = mesh.ds.x_direction_edge_flag()

        mu = self.pde.mu
        k = self.pde.k
        A11 = mu/k*eye(NE, NE, dtype=ftype)


        edge2cell = mesh.ds.edge_to_cell()
        I, = np.nonzero(~isBDEdge & isYDEdge)
        L = edge2cell[I, 0]
        R = edge2cell[I, 1]
        data = np.ones(len(I), dtype=ftype)/mesh.hx

        A12 = coo_matrix((data, (I, R)), shape=(NE, NC))
        A12 += coo_matrix((-data, (I, L)), shape=(NE, NC))

        I, = np.nonzero(~isBDEdge & isXDEdge)
        L = edge2cell[I, 0]
        R = edge2cell[I, 1]
        data = np.ones(len(I), dtype=ftype)/mesh.hy
        A12 += coo_matrix((-data, (I, R)), shape=(NE, NC))
        A12 += coo_matrix((data, (I, L)), shape=(NE, NC))
        A12 = A12.tocsr()

        
        cell2edge = mesh.ds.cell_to_cedge()
        I = np.arange(NC, dtype=itype)
        data = np.ones(NC, dtype=ftype)
        A21 = coo_matrix((data/mesh.hx, (I, cell2edge[:, 1])), shape=(NC, NE), dtype=ftype)
        A21 += coo_matrix((-data/mesh.hx, (I, cell2edge[:, 3])), shape=(NC, NE), dtype=ftype)
        A21 += coo_matrix((data/mesh.hy, (I, cell2edge[:, 2])), shape=(NC, NE), dtype=ftype)
        A21 += coo_matrix((-data/mesh.hy, (I, cell2edge[:, 0])), shape=(NC, NE), dtype=ftype)
        A21 = A21.tocsr()

        A = bmat([(A11, A12), (A21, None)], format='csr', dtype=ftype)

        return A

    def get_right_vector(self,nx,ny):
        pde = self.pde
        mesh = self.mesh
        node = mesh.node
        itype = mesh.itype
        ftype = mesh.ftype

        NN = mesh.number_of_nodes()  
        NE = mesh.number_of_edges()
        NC = mesh.number_of_cells()
        edge = mesh.entity('edge')
        isBDEdge = mesh.ds.boundary_edge_flag()
        isYDEdge = mesh.ds.y_direction_edge_flag()
        isXDEdge = mesh.ds.x_direction_edge_flag()
        bc = mesh.entity_barycenter('edge')
        pc = mesh.entity_barycenter('cell')

        mu = self.pde.mu
        k = self.pde.k

        b = np.zeros(NE+NC, dtype=ftype)

        idx, = np.nonzero(isYDEdge & isBDEdge)
        val = pde.velocity(bc[idx, :])
        b[idx] = mu/k*val[:, 0];

        idx, = np.nonzero(isXDEdge & isBDEdge)
        val = pde.velocity(bc[idx, :])
        b[idx] = mu/k*val[:, 1]

        b[NE:] = pde.source(pc)
        return b


    def solve(self):
        mesh = self.mesh
        itype = mesh.itype
        ftype = mesh.ftype

        A = self.get_left_matrix()
        b = self.get_right_vector()

        x = np.r_[self.uh, self.ph]
        b = b - A@x

        # Modify matrix
        bdIdx = np.zeros((A.shape[0],), dtype = itype)
        bdIdx[NE] = 1

        Tbd = spdiags(bdIdx, 0, A.shape[0], A.shape[1])
        T = spdiags(1-bdIdx, 0, A.shape[0], A.shape[1])
        AD = T@A@T + Tbd

        b[NE] = self.ph[0]

        # solve
        x[:] = spsolve(AD, b)
        self.uh[:] = x[:NE]
        self.ph[:] = x[NE:]

    def get_max_error(self):
        ue = np.max(np.abs(self.uh - self.uI))
        pe = np.max(np.abs(self.ph - self.pI))
        return ue, pe 
