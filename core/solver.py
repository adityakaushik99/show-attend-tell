import tensorflow as tf 
import matplotlib.pyplot as plt 
import skimage.transform
import numpy as np 
import time
import os
import _pickle as pickle 
from scipy import ndimage
from core.utils import *
from core.bleu import evaluate


class CaptioningSolver(object):
	def __init__(self, model, data, val_data, **kwargs):

		self.model = model
		self.data = data 
		self.val_data = val_data
		self.n_epochs = kwargs.pop('n_epochs', 10)
		self.batch_size = kwargs.pop('batch_size', 100)
		self.update_rule = kwargs.pop('update_rule', 'adam')
		self.learning_rate = kwargs.pop('learning_rate', 0.01)
		self.print_bleu = kwargs.pop('print_bleu', False)
		self.print_every = kwargs.pop('print_every', 100)
		self.save_every = kwargs.pop('save_every', 1)
		self.log_path = kwargs.pop('log_path','./log/')
		self.model_path = kwargs.pop('model_path', './model/')
		self.pretrained_model = kwargs.pop('pretrained_model', None)
		self.test_model = kwargs.pop('test_model', './model/lstm/model-1')

		if self.update_rule=='adam':
			self.optimizer   = tf.train.AdamOptimizer
		elif self.update_rule == 'momentum':
			self.optimizer = tf.train.MomentumOptimizer
		elif self.update_rule=='rmsprop':
			self.optimizer = tf.train.RMSPropOptimizer

		if not os.path.exists(self.model.path):
			os.makedirs(self.model_path)
		if not os.path.exists(self.log_path):
			os.makedirs(self.log_path)

	def train(self):
		n_examples = self.data['features'].shape[0]
		n_iters_per_epoch = int(np.ceil(float(n_examples)/self.batch_size))
		features = self.data['features']
		captions = self.data['captions']
		image_idxs = self.data['image_idxs']
		val_features = self.val_data['features']
		n_iters_val = int(np.ceil(float(val_features.shape[0])/self.batch_size))

		with tf.variable_scope(tf.get_variable_scope()):
			loss = self.model.build_model()
			tf.get_variable_scope().reuse_variables()
			_,_,generated_captions = self.model.build_sampler(max_len=20)

		with tf.variable_scope(tf.get_variable_scope(), reuse = False):
			optimizer = self.optimizer(learning_rate=self.learning_rate)
			grads = tf.gradients(loss, tf.trainable_variables())
			grads_and_vars = list(zip(grads, tf.trainable_variables()))
			train_op = optimizer.apply_gradients(grads_and_vars=grads_and_vars)

		tf.summary.scalar('batch_loss',loss)
		for var in tf.trainable_variables():
			tf.summary.histogram(var.op.name+'/gradient',grad)

		summary_op = tf.summary.merge_all()

		print('The number of epochs : %d '%self.n_epochs)
		print('Data size : %d'%n_examples)
		print('Batch size : %d' %self.batch_size)
		print('Iterations per epoch : %d' %n_iters_per_epoch)

		conf = tf.ConfigProto(allow_soft_placement=True)
		conf.gpu_options.allow_growth = True
		with tf.Session(config=conf) as sess:
			tf.global_variables_initializer().run()
			summary_writer = tf.summary.FileWriter(self.log_path, graph = tf.get_default_graph())
			saver = tf.train.Saver(max_to_keep=40)

			if self.pretrained_model is not None:
				print('Starting training with pretrained model')
				saver.restore(sess, self.pretrained_model)

			prev_loss = -1
			curr_loss = 0
			start_t = time.time()

			for e in range(self.n_epochs):
				rand_idxs = np.random.permutation(n_examples)
				captions = captions[rand_idxs]
				image_idxs = image_idxs[rand_idxs]

				for i in range(n_iters_per_epoch):
					captions_batch = captions[i*self.batch_size:(i+1)*self.batch_size]
					image_idxs_batch = image_idxs[i*self.batch_size:(i+1)*batch_size]
					features_batch = features[image_idxs_batch]
					feed_dict = { self.model.features:features_batch, self.model.captions:captions_batch}
					_, l = sess.run([train_op, loss], feed_dict)
					curr_loss += l

					if i%10 ==0:
						summary = sess.run(summary_op, feed_dict)
						summary_writer.add_summary(summary, e*n_iters_per_epoch+i)

					if (i+1)%self.print_every==0:
						print('\nTraining loss at epoch %d & iteration %d (mini-batch) : %.5f'%(e+1, i+1, l))
						ground_truths = captions[image_idxs==image_idxs_batch[0]]
						decoded = decode_captions(ground_truths, self.model.idx_to_word)
						for j, gt in enumerate(decoded):
							print('Ground Truth %d : %s'%(j+1, gt))
						gen_caps = sess.run(generated_captions, feed_dict)
						decoded = decode_captions(gen_caps, self.model.idx_to_word)
						print('Generated Caption : %s \n'%decoded[0])

				print('Previous Epoch loss : ', prev_loss)
				print('Current Epoch loss: ', curr_loss)
				print('Elapsed time : ', time.time()-start_t)
				prev_loss = curr_loss
				curr_loss = 0

				if self.print_bleu:
					all_gen_cap = np.ndarray((val_features.shape[0], 20))
					for i in range(n_iters_val):
						features_batch = val_features[i*self.batch_size:(i+1)*self.batch_size]
						feed_dict = {self.model.features: features_batch}
						gen_cap = sess.run(generated_captions, feed_dict = feed_dict)
						all_gen_cap[i*self.batch_size:(i+1)*self.batch_size] = gen_cap

					all_decoded = decode_captions(all_gen_cap, self.model.idx_to_word)
					save_pickle(all_decoded, "./data/val/val.candidate.captions.pkl")
					scores = evaluate(data_path = './data', split = 'val', get_scores = True)
					write_bleu(scores= scores, path = self.model_path, epoch = e)

				if (e+1)%self.save_every==0:
					saver.save(sess, os.path.join(self.model_path,'model'), global_step=e+1)
					print('Model - %s Saved'%(e+1))

	def test(self, data, split='train', attention_visualization=True, saved_sampled_captions=True):
		features = data['features']

		alphas, betas, sampled_captions = self.model.build_sampler(max_len=20)

		config = tf.ConfigProto(allow_soft_placement=True)
		config.gpu_options.allow_growth = True
		with tf.Session(config = config)as sess:
			saver = tf.train.Saver()
			saver.restore(sess, self.test_model)
			features_batch, image_files = sample_coco_minibatch(data, self.batch_size)
			feed_dict = {self.model.features:features_batch}
			alps, bets,samp_caps = sess.run([alphas, betas, sampled_captions], feed_dict)
			decoded = decode_captions(samp_caps, self.model.idx_to_word)

			if attention_visualization:
				for n in range(10):
					print('Sampled Caption : %s'%decoded[n])

					img = ndimage.imread(image_files[n])
					plt.subplot(4, 5 ,1)
					plt.imshow(img)
					plt.axis('off')

					words = decoded[n].split(" ")
					for t in range(len(words)):
						if t>18:
							break
						plt.subplot(4, 5, t+2)
						plt.test(0, 1, '%s(%.2f)'%(words[t], bets[n, t]), color = 'black', backgroundcolor = 'white', fontsize = 8)
						plt.imshow(img)
						alp_curr  = alps[n,t,:].reshape(14,14)
						alp_img = skimage.transform.pyramid_expand(alp_curr, upscale=16, sigma = 20)
						plt.imshow(alp_img, alpha=0.85)
						plt.axis('off')
					plt.show()

			if save_sampled_captions:
				all_sam_cap = np.ndarray((features.shape[0], 20))
				num_iter = int(np.ceil(float(features.shape[0])/self.batch_size))
				for i in range(num_iter):
					features_batch = features[i*self.batch_size:(i+1)*self.batch_size]
					feed_dict = {self.model.features:features_batch}
					all_sam_cap[i*self.batch.batch_size:(i+1)*self.batch_size] = sess.run(sampled_captions, feed_dict)
				all_decoded = decode_captions(all_sam_cap, self.model.idx_to_word)
				save_pickle(all_decoded, "./data/%s/%s.candidate.captions.pkl"%(split, split))