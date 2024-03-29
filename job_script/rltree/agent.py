import random
from collections import namedtuple, deque
import logging
import os

import dill as pickle
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim


device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")



class ThresholdsNetwork(nn.Module):
    """Network that will predict the new thresholds vector given a state."""


    def __init__(self, state_size, threshold_vector_size, seed, hidden_size=32):
        """Initialize parameters and build model.
        Params
        ======
            state_size (int): Dimension of each state
            threshold_vector_size (int): Dimension of each action
            seed (int): Random seed
            hidden_size (int): Number of nodes in the hidden layer
        """
        super(ThresholdsNetwork, self).__init__()
        self.seed = torch.manual_seed(seed)
        self.layer_norm = nn.LayerNorm(state_size)
        self.fc1 = nn.Linear(state_size, hidden_size)
        #self.fc2 = nn.Linear(hidden_size, hidden_size)
        self.fc3 = nn.Linear(hidden_size, threshold_vector_size)


    def forward(self, state):
        x = self.layer_norm(state)
        x = F.relu(self.fc1(x), inplace=True)
        #x = F.relu(self.fc2(x), inplace=True)
        out = F.relu(self.fc3(x))
        return out
    

    def get_thresholds_vector(self, state):
        threshold_vector = self.forward(state)#.to(device)
        return threshold_vector
    
    
class AttributeNetwork(nn.Module):
    """Network that will select a new attribute for a tree node given the environment state and thresholds vector"""

    def __init__(self, state_size, threshold_vector_size, number_of_attributes, seed, hidden_size=32):
        """Initialize parameters and build model.
        Params
        ======
            state_size (int): Dimension of each state
            threshold_vector_size (int): Dimension of each threshold vector
            seed (int): Random seed
            hidden_size (int): Number of nodes in the network's hidden layer

        """
        super(AttributeNetwork, self).__init__()
        self.seed = torch.manual_seed(seed)
        self.layer_norm = nn.LayerNorm(state_size + threshold_vector_size)
        self.fc1 = nn.Linear(state_size + threshold_vector_size, hidden_size)
        #self.fc2 = nn.Linear(hidden_size, hidden_size)
        self.fc3 = nn.Linear(hidden_size, number_of_attributes)


    def forward(self, state, threshold_vector):
        """Build a critic (attribute) network that maps (state, threshold_vector) pairs -> Q-values for each attribute."""
        x = torch.cat((state, threshold_vector), dim=-1)
        x = self.layer_norm(x)
        x = F.relu(self.fc1(x))
        #x = F.relu(self.fc2(x))
        return F.relu(self.fc3(x))


    def get_attributes_vector(self, state, threshold_vector, Xe_vect=False):
        if not Xe_vect:
            attributes_vector = self.forward(state, threshold_vector)

        else:
            # the input threshold vector is Xe instead of X
            # decompose (st,X) input into (st,Xe_k) for each k 
            attributes_vector = torch.zeros(len(threshold_vector), device=device)
            X = threshold_vector
            for k in range(len(X)):
                Xe = torch.zeros(len(X), device=device)    
                Xe[k] = X[k]
                q_vect = self.forward(state,Xe)
                attributes_vector[k] = q_vect[k]
        return attributes_vector

    
    
class ReplayBuffer:
    """Fixed-size buffer to store experience tuples."""


    def __init__(self, buffer_size, batch_size, seed):
        """Initialize a ReplayBuffer object.
        Params
        ======
            buffer_size (int): maximum size of buffer
            batch_size (int): size of each training batch
        """
        self.memory = deque(maxlen=buffer_size)  # internal memory (deque)
        self.batch_size = batch_size
        self.experience = namedtuple("Experience", field_names=["state", "action", "reward", "next_state", "done"])
        self.seed = random.seed(seed)


    def add(self, state, action, reward, next_state, done):
        """Add a new experience to memory."""
        e = self.experience(state, action, reward, next_state, done)
        self.memory.append(e)


    def sample(self):
        """Randomly sample a batch of experiences from memory."""
        experiences = random.sample(self.memory, k=self.batch_size)
        states = torch.vstack([e.state for e in experiences if e is not None])
        actions = torch.vstack([torch.tensor(e.action, device=device) for e in experiences if e is not None])
        rewards = torch.vstack([torch.tensor(e.reward, device=device) for e in experiences if e is not None])
        next_states = torch.vstack([e.next_state for e in experiences if e is not None])
        dones = torch.vstack([torch.tensor(e.done, device=device) for e in experiences if e is not None])
        return zip(states, actions, rewards, next_states, dones)


    def __len__(self):
        """Return the current size of internal memory."""
        return len(self.memory)

    
class Agent():
    """Interacts with and learns from the environment."""
    

    def __init__(self, state_size, threshold_vector_size, number_of_attributes, random_seed, hidden_size, lr_actor, lr_critic, buffer_size, batch_size, gamma, curdir):
        """Initialize an Agent object.
        
        Params
        ======
            state_size (int): dimension of each state
            threshold_vector_size (int): dimension of each threshold vector
            random_seed (int): random seed
            add rest
        """
        self.logger = logging.getLogger('Agent')
        self.state_size = state_size
        self.threshold_vector_size = threshold_vector_size
        self.number_of_attributes = number_of_attributes
        self.seed = random.seed(random_seed)
        self.gamma = gamma
        self.curdir = curdir
        #print("Using: ", device)
        self.logger.info("using {}".format(str(device)))

        # Thresholds Network 
        self.logger.debug("creating Thresholds network")
        self.ThresholdsNetwork = ThresholdsNetwork(state_size, threshold_vector_size, random_seed, hidden_size).to(device)
        self.optimizer_ThresholdsNetwork = optim.Adam(self.ThresholdsNetwork.parameters(), lr=lr_actor)     
        
        # Attribute Network  
        self.logger.debug("creating Attribute network")
        self.AttributeNetwork = AttributeNetwork(state_size, threshold_vector_size, number_of_attributes, random_seed, hidden_size).to(device)
        self.optimizer_AttributeNetwork = optim.Adam(self.AttributeNetwork.parameters(), lr=lr_critic)#, weight_decay=0)

        # Replay memory
        self.logger.debug("creating replay buffer")
        self.memory = ReplayBuffer(buffer_size, batch_size, random_seed)
        

    def act(self, state, eps=0.2):
        """Returns actions for given state as per current policy."""
        # greedy epsilon with param eps
        self.logger.debug("deciding action")
        thresholds_vector = self.ThresholdsNetwork.get_thresholds_vector(state)

        p = np.random.random() 
        if p<eps:
            index_selected_attribute = random.choice(range(self.number_of_attributes))
        else:
            attributes_vector = self.AttributeNetwork.get_attributes_vector(state, thresholds_vector)
            index_selected_attribute = torch.argmax(attributes_vector)
        action = (index_selected_attribute, thresholds_vector.squeeze(0)[index_selected_attribute])

        return action


    def step(self, state, action, reward, next_state, done):
        """Save experience in replay memory, and use random sample from buffer to learn."""
        # Save experience / reward
        self.logger.debug("storing experience")
        self.memory.add(state, action, reward, next_state, done)

        # if enough samples are available in memory
        # sample a minibatch and learn/update networks
        if len(self.memory) > self.memory.batch_size:
            experiences = self.memory.sample()
            self.learn(experiences, self.gamma)
        else:
            self.logger.debug("not enough samples to learn yet")



    def learn(self, experiences, gamma):
        """Updates the two neural networks using given batch of experience tuples.
            thresholds_target(state) -> Xt vector
            attributes_target(state, Xt) -> Q-value
            see paper/report for notation
        Params
        ======
            experiences (Tuple[torch.Tensor]): tuple of (s, a, r, s', done) tuples 
            gamma (float): discount factor
        """
        self.logger.debug("calculating loss values")

        # Q_loss, X_loss = [], []
        Q_loss = torch.zeros(self.memory.batch_size, device=device)
        X_loss = torch.zeros(self.memory.batch_size, device=device)
    
        for iter, experience in enumerate(experiences): # for b in B
            state, action, reward, next_state, done = experience
            sb = state
            k_act = int(action[0].item())
            rb = reward
            sb1 = next_state # S_{b+1}
            Xb1 = self.ThresholdsNetwork.get_thresholds_vector(sb1) # X_{b+1}
            Xb = self.ThresholdsNetwork.get_thresholds_vector(sb) # X_{b}

            # yb calc

            # # get max_k(Qq)
            # Qq = []
            # ??? why not simply call self.AttributeNetwork.forward(sb1, Xb1)?
            # for k in range(self.number_of_attributes):
            #     # get Xe_{b+1}
            #     Xeb1k = torch.zeros(len(Xb1)).to(device)
            #     Xeb1k[k] = Xb1[k]
            #     qek = torch.max(self.AttributeNetwork.get_attributes_vector(sb1, Xeb1k, Xe_vect=True))
            #     Qq.append(qek[k])
            # maxQq = torch.max(torch.Tensor(Qq))

            # we iterate and pass each Xeb1k seperately because the attribute network should calculate the Q value for a a state and a single threshold, not all possible thresholds.
            # => testing here anyway
            Qq = self.AttributeNetwork.forward(sb1, Xb1)
            maxQq = torch.max(Qq)

            if done: # terminal node
                yb = rb
            else:
                yb = rb + gamma * maxQq

            # compute losses for single transitions

            # Q loss
            xebk = torch.zeros(len(Xb), device=device)
            xebk[k_act] = Xb[k_act]
            Q_loss[iter] = yb - self.AttributeNetwork.get_attributes_vector(sb, xebk, Xe_vect=True)[k_act]
            
            # # X loss
            # sum_Qq = 0
            # for k in range(self.number_of_attributes):
            #     Xebk = torch.zeros(len(Xb)).to(device)
            #     Xebk[k] = Xb[k] 
            #     # Xe_{b,k}
            #     qek = self.AttributeNetwork.get_attributes_vector(sb1, Xebk, Xe_vect=True)
            #     sum_Qq += qek[k]            
            # X_loss.append(-sum_Qq)

            qek = self.AttributeNetwork.get_attributes_vector(sb1, Xb, Xe_vect=False)
            X_loss[iter] = qek.sum()

        # compute losses as expectation over the experiences batch and update networks

        self.logger.debug("updating thresholds network")
        # update thresholds network
        # Compute loss
        loss_thresholds_network = X_loss.mean()
        loss_thresholds_network.requires_grad_()
        # Minimize the loss
        self.optimizer_ThresholdsNetwork.zero_grad()
        loss_thresholds_network.backward()
        self.optimizer_ThresholdsNetwork.step()

        self.logger.debug("updating attribute_network")
        # update attribute network
        # Compute loss
        loss_attribute_network = Q_loss.mean().detach()
        loss_attribute_network.requires_grad_()
        # Minimize the loss
        self.optimizer_AttributeNetwork.zero_grad()
        loss_attribute_network.backward()
        self.optimizer_AttributeNetwork.step()

    
    def load_checkpoint(self, ep):
        # thresholds Network 
        self.logger.debug("loading thresholds Network")
        actor_net_path = os.path.join(self.curdir, "checkpoints", f"ep{ep}-thresholds-network.pth")
        checkpoint = torch.load(actor_net_path)
        self.ThresholdsNetwork.load_state_dict(checkpoint['model_state_dict'])
        self.optimizer_ThresholdsNetwork.load_state_dict(checkpoint['optimizer_state_dict'])

        # attribute Network  
        self.logger.debug("loading attribute Network")
        actor_net_path = os.path.join(self.curdir, "checkpoints", f"ep{ep}-attribute-network.pth")
        checkpoint = torch.load(actor_net_path)
        self.AttributeNetwork.load_state_dict(checkpoint['model_state_dict'])
        self.optimizer_AttributeNetwork.load_state_dict(checkpoint['optimizer_state_dict'])
        
        # Replay memory
        self.logger.debug("loading memory")
        with open(os.path.join(self.curdir, "checkpoints", f"ep{ep}-memory.pkl"),'rb') as f:
            self.memory.memory = pickle.load(f)
    

    def save_checkpoint(self, ep):
        # thresholds Network 
        self.logger.debug("saving thresholds Network")
        torch.save({
            'model_state_dict': self.ThresholdsNetwork.state_dict(),
            'optimizer_state_dict': self.optimizer_ThresholdsNetwork.state_dict(),
            }, os.path.join(self.curdir, "checkpoints", f"ep{ep}-thresholds-Network.pth"))
        
        # attribute Network  
        self.logger.debug("saving attribute Network")
        torch.save({
            'model_state_dict': self.AttributeNetwork.state_dict(),
            'optimizer_state_dict': self.optimizer_AttributeNetwork.state_dict(),
            }, os.path.join(self.curdir, "checkpoints", f"ep{ep}-attribute-Network.pth"))

        # Replay memory
        self.logger.debug("saving memory")
        with open(os.path.join(self.curdir, "checkpoints", f"ep{ep}-memory.pkl"),'wb') as f:
            pickle.dump(self.memory.memory, f)

