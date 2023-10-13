"""
Where we would put our implementation of Deep Clustering with Dual Path RNN

# EDIT: Below are intial thoughts on how to arrange the modules and code.
"""
import torch
import torch.nn as nn
from torch.nn.utils.rnn import pad_packed_sequence, pack_sequence, pad_sequence
import torch.nn.functional as F


class DPCL_DPRNN(nn.Module):
    '''
        Implement of Deep Clustering with DPRNN version 0.0.1? 

        NOTE: Initial attempt.
        The biggest blockers are:
            1. format mismatch, DPRNN code uses time-domain, DPCL uses time-frequency
                1.1 Consequence of this is that their dataloaders are used differently
                1.2 DPCL uses STFT which changes the shape of the data from DPRNN
            2. DPRNN code has conv layers integrated (not specified in DPRNN paper)
        in_channels = nfft
        out_channels = nfft # Conv layers hidden states removed
        hidden_channels = hidden_cells

    '''
    # ORIGINAL: def __init__(self, num_layer=2, nfft=129, hidden_cells=600, emb_D=40, dropout=0.0, bidirectional=True, activation="Tanh"):

    def __init__(self, in_channels, hidden_channels,
                 dropout=0.0, bidirectional=True, activation="Tanh",
                 rnn_type="LSTM", norm='ln', num_layers=6,
                 num_spks=2, emb_D=40,
                 K=250,
                 ):
        super(DPCL_DPRNN, self).__init__()
        self.emb_D = emb_D
        # The goal is to replace this BLSTM layer, below is the propsed replacement
        # ORIGINAL: self.blstm = nn.LSTM(input_size=nfft, hidden_size=hidden_cells, num_layers=num_layer, batch_first=True,
        #                      dropout=dropout, bidirectional=bidirectional)
        self.dprnn = Dual_Path_RNN(in_channels, hidden_channels,
                                   rnn_type, norm, dropout, bidirectional=True, num_layers=num_layers, K=K,
                                   num_spks=num_spks)

        self.dropout = nn.Dropout(dropout)
        self.activation = getattr(torch.nn, activation)()
        self.linear = nn.Linear(
            2*hidden_channels if bidirectional else hidden_channels, in_channels * emb_D)

        self.D = emb_D

    # It seems na iba yung shape na ginagamit ng original DPCL compared sa expected shape ng DPRNN
    # Dual_Path_RNN() forward pass expects -> x: [B, N, L]
    # DPCL forward pass expects -> x: [B, T, F] | BATCH, TIME, FREQUENCY
    # From the Luo's Paper, N = feature dimensions, L= number of time steps -> equivalent ba sa T & F?
    # L is also descibed as "input length"
    # In DPCL, T = Time?, F = Frequency?
    # B is batch in both cases (90% sure)
    def forward(self, x, is_train=True):
        '''
           input: 
                  for train: B x T x F
                  for test: T x F
           return: 
                  for train: B x TF x D
                  for test: TF x D
        '''
        #print("x.shape before not is_train ", x.data.size())
        # It takes in a 2dim tensor [?, NFFT]
        if not is_train:
            x = torch.unsqueeze(x, 0)
        # B x T x F -> B x T x hidden
        # x, _ = self.blstm(x)  # ORIGINAL
        # Unpack sequence first
        #print("x.shape before is_train ", x.data.size())

        # DPRNN will not output hidden states (x, _ = self.blstm())
        #print("x.shape before self.dprnn ", x.data.size())
        x = self.dprnn(x)  # DPRNN takes x and outputs x with same shape

        if is_train:
            # It gets transformed back to a 3 dim tensor here [B, T, F]
            x, _ = pad_packed_sequence(x, batch_first=True)
            #print("x.shape is_train triggered", x.data.size())
        # if is_train:
        #     x, _ = pad_packed_sequence(x, batch_first=True)

        x = self.dropout(x)
        x = x.permute(0, 2, 1)
        #print("x.shape before self.linear ", x.data.size())
        # B x T x hidden -> B x T x FD
        x = self.linear(x)
        x = self.activation(x)
        #print("x.shape after self.activation ", x.data.size())
        B = x.shape[0]
        if is_train:
            # B x TF x D
            x = x.view(B, -1, self.D)
        else:
            # B x TF x D -> TF x D
            x = x.view(-1, self.D)

        return x

# I'm not sure how the normalization work pero its important daw,
# moreover im not sure which one is the best but the default is "ln"


class GlobalLayerNorm(nn.Module):
    '''
       Calculate Global Layer Normalization
       dim: (int or list or torch.Size) –
          input shape from an expected input of size
       eps: a value added to the denominator for numerical stability.
       elementwise_affine: a boolean value that when set to True,
          this module has learnable per-element affine parameters
          initialized to ones (for weights) and zeros (for biases).
    '''

    def __init__(self, dim, shape, eps=1e-8, elementwise_affine=True):
        super(GlobalLayerNorm, self).__init__()
        self.dim = dim
        self.eps = eps
        self.elementwise_affine = elementwise_affine

        if self.elementwise_affine:
            if shape == 3:
                self.weight = nn.Parameter(torch.ones(self.dim, 1))
                self.bias = nn.Parameter(torch.zeros(self.dim, 1))
            if shape == 4:
                self.weight = nn.Parameter(torch.ones(self.dim, 1, 1))
                self.bias = nn.Parameter(torch.zeros(self.dim, 1, 1))
        else:
            self.register_parameter('weight', None)
            self.register_parameter('bias', None)

    def forward(self, x):
        # x = N x C x K x S or N x C x L
        # N x 1 x 1
        # cln: mean,var N x 1 x K x S
        # gln: mean,var N x 1 x 1
        if x.data.dim() == 4:
            mean = torch.mean(x, (1, 2, 3), keepdim=True)
            var = torch.mean((x-mean)**2, (1, 2, 3), keepdim=True)
            if self.elementwise_affine:
                x = self.weight*(x-mean)/torch.sqrt(var+self.eps)+self.bias
            else:
                x = (x-mean)/torch.sqrt(var+self.eps)
        if x.data.dim() == 3:
            mean = torch.mean(x, (1, 2), keepdim=True)
            var = torch.mean((x-mean)**2, (1, 2), keepdim=True)
            if self.elementwise_affine:
                x = self.weight*(x-mean)/torch.sqrt(var+self.eps)+self.bias
            else:
                x = (x-mean)/torch.sqrt(var+self.eps)
        return x


class CumulativeLayerNorm(nn.LayerNorm):
    '''
        Calculate Cumulative Layer Normalization
        dim: you want to norm dim
        elementwise_affine: learnable per-element affine parameters
    '''

    def __init__(self, dim, elementwise_affine=True):
        super(CumulativeLayerNorm, self).__init__(
            dim, elementwise_affine=elementwise_affine, eps=1e-8)

    def forward(self, x):
        # x: N x C x K x S or N x C x L
        # N x K x S x C
        if x.data.dim() == 4:
            x = x.permute(0, 2, 3, 1).contiguous()
            # N x K x S x C == only channel norm
            x = super().forward(x)
            # N x C x K x S
            x = x.permute(0, 3, 1, 2).contiguous()
        if x.data.dim() == 3:
            x = torch.transpose(x, 1, 2)
            # N x L x C == only channel norm
            x = super().forward(x)
            # N x C x L
            x = torch.transpose(x, 1, 2)
        return x


def select_norm(norm, dim, shape):
    if norm == 'gln':
        return GlobalLayerNorm(dim, shape, elementwise_affine=True)
    if norm == 'cln':
        return CumulativeLayerNorm(dim, elementwise_affine=True)
    if norm == 'ln':
        return nn.GroupNorm(1, dim, eps=1e-8)
    else:
        return nn.BatchNorm1d(dim)


class Dual_RNN_Block(nn.Module):  # Corresponds to only B) # This block is standalone
    '''
       Implementation of the intra-RNN and the inter-RNN
       input:
            in_channels: The number of expected features in the input x
            out_channels: The number of features in the hidden state h
            rnn_type: RNN, LSTM, GRU
            norm: gln = "Global Norm", cln = "Cumulative Norm", ln = "Layer Norm"
            dropout: If non-zero, introduces a Dropout layer on the outputs 
                     of each LSTM layer except the last layer, 
                     with dropout probability equal to dropout. Default: 0
            bidirectional: If True, becomes a bidirectional LSTM. Default: False
    '''
    # Out channels refer to the out channels by the CONV layers
    # the  first argument in nn.LSTM is the input size, which is equivalent to nfft in the original DPCL

    def __init__(self, in_channels,
                 hiddden_cells, rnn_type='LSTM', norm='gln',
                 dropout=0, bidirectional=False, num_spks=2):
        super(Dual_RNN_Block, self).__init__()
        # RNN model
        # getattr() gets nn.<RNN_TYPE> and then intializes it with the args, so its like nn.BLSTM(*args, **kwargs)
        self.intra_rnn = getattr(nn, rnn_type)(
            input_size=hiddden_cells*2 if bidirectional else hiddden_cells, hidden_size=hiddden_cells, num_layers=1, batch_first=True, dropout=dropout, bidirectional=bidirectional)
        self.inter_rnn = getattr(nn, rnn_type)(
            input_size=hiddden_cells*2 if bidirectional else hiddden_cells, hidden_size=hiddden_cells, num_layers=1, batch_first=True, dropout=dropout, bidirectional=bidirectional)
        # Norm
        self.intra_norm = select_norm(
            norm, hiddden_cells*2 if bidirectional else hiddden_cells, 4)  # in the
        self.inter_norm = select_norm(
            norm, hiddden_cells*2 if bidirectional else hiddden_cells, 4)  # in the
        # Linear
        self.intra_linear = nn.Linear(
            hiddden_cells*2 if bidirectional else hiddden_cells,  hiddden_cells*2 if bidirectional else hiddden_cells)
        self.inter_linear = nn.Linear(
            hiddden_cells*2 if bidirectional else hiddden_cells,  hiddden_cells*2 if bidirectional else hiddden_cells)

    def forward(self, x):
        '''
         B: BATCH
         N: feature dims 
         K: Length of the chunks (from L length of segment) 
         S: Number of chunks
           x: [B, N, K, S]
           out: [Spks, B, N, K, S]
        '''
        B, N, K, S = x.shape
        # Based on my reading permute() rearanges the order of the dims, view() reshapes
        # intra RNN
        # [BS, K, N] # Preparing the shape to feed into the intra chunk(?)
        intra_rnn = x.permute(0, 3, 2, 1).contiguous().view(B*S, K, N)
        # [BS, K, H]
        # Gets the type of rnn then feeds the data
        #print("intra_rnn.shape before intra rnn: ", intra_rnn.shape)
        intra_rnn, _ = self.intra_rnn(intra_rnn)
        #print("intra_rnn.shape after intra rnn: ", intra_rnn.shape)
        # [BS, K, N]
        intra_rnn = self.intra_linear(
            intra_rnn.contiguous().view(B*S*K, -1)).view(B*S, K, -1)
        #print("intra_rnn.shape after intra_linear: ", intra_rnn.shape)

        # [B, S, K, N]
        intra_rnn = intra_rnn.view(B, S, K, -1)  # Infer the correct size N
        #print("intra_rnn.shape after intra_rnn.view: ", intra_rnn.shape)

        # [B, N, K, S]
        intra_rnn = intra_rnn.permute(0, 3, 2, 1).contiguous()
        #print("intra_rnn.shape before intra_norm: ", intra_rnn.shape)
        intra_rnn = self.intra_norm(intra_rnn)

        # [B, N, K, S]
        #print("intra_rnn.shape after intra_norm: ", intra_rnn.shape)
        # adds the processed input back to the original input
        intra_rnn = intra_rnn + x

        # inter RNN
        # [BK, S, N] # Prepares shape, note 2 and 3 are exchanged this time?
        inter_rnn = intra_rnn.permute(0, 2, 3, 1).contiguous().view(B*K, S, N)
        # [BK, S, H]
        # Gets the type of rnn then feeds the data
        #print("inter_rnn.shape before inter rnn: ", inter_rnn.shape)
        inter_rnn, _ = self.inter_rnn(inter_rnn)
        #print("inter_rnn.shape after inter rnn: ", inter_rnn.shape)

        # [BK, S, N]
        inter_rnn = self.inter_linear(
            inter_rnn.contiguous().view(B*S*K, -1)).view(B*K, S, -1)
        #print("inter_rnn.shape after inter_rnn : ", inter_rnn.shape)

        # [B, K, S, N]
        inter_rnn = inter_rnn.view(B, K, S, N)
        # [B, N, K, S]
        inter_rnn = inter_rnn.permute(0, 3, 1, 2).contiguous()
        inter_rnn = self.inter_norm(inter_rnn)
        # [B, N, K, S]
        # intra_rnn (tensor) is the "original" input that was fed to the inter rnn (layers)
        out = inter_rnn + intra_rnn
        #print("out.shape after out = inter_rnn + intra_rnn: ", out.shape)

        return out


class Dual_Path_RNN(nn.Module):  # The DPRNN block all together # Has conv tasnet layers
    '''
       Implementation of the Dual-Path-RNN model 
       input:
            in_channels: The number of expected features in the input x
            out_channels: The number of features in the hidden state h
            rnn_type: RNN, LSTM, GRU
            norm: gln = "Global Norm", cln = "Cumulative Norm", ln = "Layer Norm"
            dropout: If non-zero, introduces a Dropout layer on the outputs 
                     of each LSTM layer except the last layer, 
                     with dropout probability equal to dropout. Default: 0
            bidirectional: If True, becomes a bidirectional LSTM. Default: False
            num_layers: number of Dual-Path-Block
            K: the length of chunk
            num_spks: the number of speakers
    '''

    def __init__(self, in_channels, hidden_channels,
                 rnn_type='LSTM', norm='ln', dropout=0,
                 bidirectional=False, num_layers=4, K=200, num_spks=2):
        super(Dual_Path_RNN, self).__init__()
        self.K = K
        self.num_spks = num_spks
        self.num_layers = num_layers
        self.norm = select_norm(norm, in_channels, 3)
        self.linear_2_hidden = nn.Linear(
            in_channels, 2*hidden_channels if bidirectional else hidden_channels)
        # self.conv1d = nn.Conv1d(in_channels, out_channels, 1, bias=False)

        self.dual_rnn = nn.ModuleList([])
        for i in range(num_layers):
            #print("hidden_channels", hidden_channels,
            #      "nfft", in_channels, "layer: ", i)
            self.dual_rnn.append(Dual_RNN_Block(in_channels, hidden_channels,
                                                rnn_type=rnn_type, norm=norm, dropout=dropout,
                                                bidirectional=bidirectional,))

        # self.conv2d = nn.Conv2d(
        #     out_channels, out_channels*num_spks, kernel_size=1)
        # self.end_conv1x1 = nn.Conv1d(out_channels, in_channels, 1, bias=False)
        # self.prelu = nn.PReLU()
        # self.activation = nn.ReLU()
        # gated output layer
        # self.output = nn.Sequential(nn.Conv1d(out_channels, out_channels, 1),
        #                             nn.Tanh()
        #                             )
        # self.output_gate = nn.Sequential(nn.Conv1d(out_channels, out_channels, 1),
        #                                  nn.Sigmoid()
        #                                  )

    def forward(self, x):
        '''
           x: [B, N, L]

        '''
        # Current [BT, F]
        x = self.norm(x)
        #print("x.shape after norm: ", x.data.shape)

        # transform into [B, T(L), F(N)]
        x, _ = pad_packed_sequence(x, batch_first=True)
        #print("x.shape after pad_packed_sequence: ", x.shape)
        # [B, N, L]
        # The convolutional layers, prelu, relu, etc., is not specified in the DPRNN paper
        # x = self.conv1d(x)
        # print("AFTER CONV1D")
        # TODO ADD SELF.LINEAR TO 600 HERE
        x = self.linear_2_hidden(x)
        # [B, N, K, S]
        # N in DPRNN might be equivalent to nfft in DPCL
        # If so, this reordering would make sense, and the inputsize would be predictable in the DPRNN block
        # transform into [B, N(F), L(T)]
        x = x.data.permute(0, 2, 1).contiguous()
        #print("x.shape before _Segmentation", x.shape)
        x, gap = self._Segmentation(x, self.K)
        # [B, N*spks, K, S]
        # [Batch, ?, Chunk size, ?]
        #print("x.shape after _Segmentation", x.shape)
        for i in range(self.num_layers):  # its gonna make 6 instances of a dprnn block
            x = self.dual_rnn[i](x)
        # x = self.prelu(x)
        # x = self.conv2d(x)
        # [B*spks, N, K, S]
        B, N, K, S = x.shape
        #print("x.shape after x = self.dual_rnn[i](x)", x.shape)
        # I stopped here 9/16/2023
        # TODO, change how .view() rearranges the tensors to match what it was orginally trying to do
        # It gave me this error so far:
        # in forward \n    x = x.view(B*self.num_spks, -1, K, S)
        # RuntimeError: shape '[8, -1, 10, 150]' is invalid for input of size 774000
        # Source of this error is view() retains the number of elements in the tensor,
        # by doing B*num_spks the n of elements are mismatched
        # Removing the parts that adds the dims for the num of speakers allows it to pass through
        # We may not need the dims for the num of speakers (might be a ConvTasNet thing)

        # x = x.view(B*self.num_spks, N, K, S)
        # [B*spks, N, L]
        x = self._over_add(x, gap)
        #print("x.shape after _over_add", x.shape)

        # x = self.output(x)*self.output_gate(x)
        # [spks*B, N, L]
        # x = self.end_conv1x1(x)
        # [B*spks, N, L] -> [B, spks, N, L]
        # _, N, L = x.shape
        # x = x.view(B, self.num_spks, N, L)
        # x = self.activation(x) # there is an activation outside the block
        # [spks, B, N, L]
        # x = x.transpose(0, 1)
        # return to original order, DPCL will do B x T x hidden -> B x T x FD, if left untouched it will do B x F x TD
        x = x.permute(0, 1, 2).contiguous()
        x = pack_sequence(x)
        #print("x.shape after permute before return", x.data.shape)
        return x

    def _padding(self, input, K):
        '''
           padding the audio times
           K: length of chunks
           P: hop size
           input: [B, N, L] # N = feature dims, L=Length of segment
        '''
        B, N, L = input.shape
        P = K // 2
        gap = K - (P + L % K) % K
        if gap > 0:
            pad = torch.Tensor(torch.zeros(B, N, gap)).type(input.type())
            input = torch.cat([input, pad], dim=2)

        _pad = torch.Tensor(torch.zeros(B, N, P)).type(input.type())
        input = torch.cat([_pad, input, _pad], dim=2)

        return input, gap

    def _Segmentation(self, input, K):  # Corresponds to A)
        '''
           the segmentation stage splits
           K: chunks of length
           P: hop size
           input: [B, N, L]
           output: [B, N, K, S]
        '''
        B, N, L = input.shape
        P = K // 2
        # padding may not be needed, as the input would have gone through pre processing with STFT
        input, gap = self._padding(input, K)
        # [B, N, K, S]
        input1 = input[:, :, :-P].contiguous().view(B, N, -1, K)
        input2 = input[:, :, P:].contiguous().view(B, N, -1, K)
        input = torch.cat([input1, input2], dim=3).view(
            B, N, -1, K).transpose(2, 3)  # transforms into 3D tensor

        return input.contiguous(), gap

    def _over_add(self, input, gap):  # Corresponds to C)
        '''
           Merge sequence
           input: [B, N, K, S]
           gap: padding length
           output: [B, N, L]
        '''
        B, N, K, S = input.shape
        P = K // 2
        # [B, N, S, K]
        input = input.transpose(2, 3).contiguous().view(B, N, -1, K * 2)

        input1 = input[:, :, :, :K].contiguous().view(B, N, -1)[:, :, P:]
        input2 = input[:, :, :, K:].contiguous().view(B, N, -1)[:, :, :-P]
        input = input1 + input2
        # [B, N, L]
        if gap > 0:
            input = input[:, :, :-gap]

        return input

# Not used, see encoder - separation - decoder flow
# class Dual_RNN_model(nn.Module):  # With conv tasnet
#     '''
#        model of Dual Path RNN # with conv-tasnet?, the encoder / decoder model was a tasnet specific thing iirc

#        input:
#             in_channels: The number of expected features in the input x
#             out_channels: The number of features in the hidden state h
#             hidden_channels: The hidden size of RNN
#             kernel_size: Encoder and Decoder Kernel size
#             rnn_type: RNN, LSTM, GRU
#             norm: gln = "Global Norm", cln = "Cumulative Norm", ln = "Layer Norm"
#             dropout: If non-zero, introduces a Dropout layer on the outputs
#                      of each LSTM layer except the last layer,
#                      with dropout probability equal to dropout. Default: 0
#             bidirectional: If True, becomes a bidirectional LSTM. Default: False
#             num_layers: number of Dual-Path-Block
#             K: the length of chunk
#             num_spks: the number of speakers
#     '''

#     def __init__(self, in_channels, out_channels, hidden_channels,
#                  kernel_size=2, rnn_type='LSTM', norm='ln', dropout=0,
#                  bidirectional=False, num_layers=4, K=200, num_spks=2):
#         super(Dual_RNN_model, self).__init__()
#         self.encoder = Encoder(kernel_size=kernel_size,
#                                out_channels=in_channels)
#         self.separation = Dual_Path_RNN(in_channels, out_channels, hidden_channels,
#                                         rnn_type=rnn_type, norm=norm, dropout=dropout,
#                                         bidirectional=bidirectional, num_layers=num_layers, K=K, num_spks=num_spks)
#         self.decoder = Decoder(in_channels=in_channels, out_channels=1,
#                                kernel_size=kernel_size, stride=kernel_size//2, bias=False)
#         self.num_spks = num_spks

#     def forward(self, x):
#         '''
#            x: [B, L]
#         '''
#         # [B, N, L]
#         e = self.encoder(x)
#         # [spks, B, N, L]
#         s = self.separation(e)
#         # [B, N, L] -> [B, L]
#         out = [s[i]*e for i in range(self.num_spks)]
#         audio = [self.decoder(out[i]) for i in range(self.num_spks)]
#         return audio